import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** A local, shell-owned UiAutomation session. No APK modification or network listener. */
public final class HongguoUi {
    static final String PACKAGE = "com.phoenix.read";
    static Object ui;
    static volatile String activity = "";
    static final Class<?>[] NO_TYPES = new Class<?>[0];
    static final Object[] NO_ARGS = new Object[0];
    static final ConcurrentHashMap<Class<?>, ConcurrentHashMap<String, Method>> METHODS = new ConcurrentHashMap<>();
    static Class<?> cls(String name) throws Exception { return Class.forName(name); }
    static Method method(Class<?> type, String name, Class<?>[] types) throws Exception {
        ConcurrentHashMap<String, Method> methods=METHODS.get(type);
        if(methods==null) {
            ConcurrentHashMap<String, Method> created=new ConcurrentHashMap<>();
            methods=METHODS.putIfAbsent(type,created);
            if(methods==null) methods=created;
        }
        String key=types.length==0 ? name : name+Arrays.toString(types);
        Method found=methods.get(key);
        if(found==null) {
            Method resolved=type.getMethod(name,types);
            found=methods.putIfAbsent(key,resolved);
            if(found==null) found=resolved;
        }
        return found;
    }
    static Object call(Object obj, String name, Class<?>[] types, Object... args) throws Exception {
        return method(obj.getClass(),name,types).invoke(obj,args);
    }
    static Object get(Object obj, String name) throws Exception { return call(obj,name,NO_TYPES,NO_ARGS); }
    static String str(Object obj) { return obj == null ? "" : obj.toString(); }
    static String quote(Object obj) {
        String s = str(obj); StringBuilder b = new StringBuilder("\"");
        for (int i=0; i<s.length(); i++) {
            char c=s.charAt(i);
            if (c=='"' || c=='\\') b.append('\\').append(c);
            else if (c<' ') b.append(String.format("\\u%04x", (int)c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
    static final class RectFields {
        static final Class<?> TYPE;
        static final Class<?>[] TYPES;
        static final Constructor<?> CONSTRUCTOR;
        static final Field LEFT, TOP, RIGHT, BOTTOM;
        static {
            try {
                TYPE=Class.forName("android.graphics.Rect");
                TYPES=new Class<?>[]{TYPE};
                CONSTRUCTOR=TYPE.getConstructor();
                LEFT=TYPE.getField("left");TOP=TYPE.getField("top");
                RIGHT=TYPE.getField("right");BOTTOM=TYPE.getField("bottom");
            } catch(ReflectiveOperationException e) { throw new ExceptionInInitializerError(e); }
        }
    }
    static String bounds(Object n) throws Exception {
        Object r=RectFields.CONSTRUCTOR.newInstance(NO_ARGS);
        call(n,"getBoundsInScreen",RectFields.TYPES,r);
        return "["+RectFields.LEFT.getInt(r)+","+RectFields.TOP.getInt(r)+","+
            RectFields.RIGHT.getInt(r)+","+RectFields.BOTTOM.getInt(r)+"]";
    }
    static void clearUiCache() throws Exception {
        // A rotated WSA surface can precede accessibility invalidation by
        // several seconds. Refresh this bridge's node/window cache before a
        // read or a checked action; this never changes APP data or its cache.
        try {
            get(ui,"clearCache"); // Public on Android 14+.
        } catch(NoSuchMethodException olderAndroid) {
            Class<?> type=cls("android.view.accessibility.AccessibilityInteractionClient");
            Object client=method(type,"getInstance",NO_TYPES).invoke(null,NO_ARGS);
            try {
                call(client,"clearCache",new Class<?>[]{int.class},get(ui,"getConnectionId"));
            } catch(NoSuchMethodException legacyAndroid) {
                get(client,"clearCache");
            }
        }
    }
    static List<Object> roots() throws Exception {
        clearUiCache();
        List<Object> out=new ArrayList<>();
        for(Object w:(List<?>)get(ui,"getWindows")) {
            try {
                Object root=get(w,"getRoot");
                if(root != null) {
                    if(PACKAGE.equals(str(get(root,"getPackageName")))) out.add(root);
                    else get(root,"recycle");
                }
            } finally { get(w,"recycle"); }
        }
        if(out.isEmpty()) {
            Object root=get(ui,"getRootInActiveWindow");
            if(root!=null) {
                if(PACKAGE.equals(str(get(root,"getPackageName")))) out.add(root);
                else get(root,"recycle");
            }
        }
        return out;
    }
    static void walk(Object node, String path, List<String> nodes, int depth, int[] visited) throws Exception {
        if(node==null || depth>40 || nodes.size()>1200) return;
        visited[0]++;
        if((Boolean)get(node,"isVisibleToUser")) {
            nodes.add("{\"path\":"+quote(path)+",\"id\":"+quote(get(node,"getViewIdResourceName"))+
                ",\"text\":"+quote(get(node,"getText"))+",\"desc\":"+quote(get(node,"getContentDescription"))+
                ",\"class\":"+quote(get(node,"getClassName"))+",\"clickable\":"+get(node,"isClickable")+
                ",\"enabled\":"+get(node,"isEnabled")+",\"selected\":"+get(node,"isSelected")+
                ",\"checked\":"+get(node,"isChecked")+",\"bounds\":"+bounds(node)+"}");
        }
        int count=(Integer)get(node,"getChildCount");
        for(int i=0;i<count;i++) {
            Object child=call(node,"getChild",new Class<?>[]{int.class},i);
            if(child!=null) {
                try { walk(child,path+"/"+i,nodes,depth+1,visited); }
                finally { get(child,"recycle"); }
            }
        }
    }
    static String snapshot() throws Exception {
        long started=System.nanoTime();
        List<String> nodes=new ArrayList<>(); List<Object> rs=roots();
        int[] visited={0}; int selected=-1;
        try {
            for(int i=0;i<rs.size();i++) {
                walk(rs.get(i),String.valueOf(i),nodes,0,visited);
                // Python uses the first emitted root only. Empty top windows
                // must not hide the primary player, and paths keep their real
                // root index so a later action still resolves the same node.
                if(!nodes.isEmpty()) { selected=i;break; }
            }
        } finally { for(Object root:rs) get(root,"recycle"); }
        String payload="{\"ok\":true,\"activity\":"+quote(activity)+",\"playing\":null,\"nodes\":["+
            String.join(",",nodes)+"]";
        return payload+",\"diagnostics\":{\"snapshot_ms\":"+(System.nanoTime()-started)/1_000_000.0+
            ",\"rootCount\":"+rs.size()+",\"rootIndex\":"+selected+",\"visited\":"+visited[0]+
            ",\"nodes\":"+nodes.size()+"}}";
    }
    static String decode(String s) throws Exception { return new String(Base64.getDecoder().decode(s),"UTF-8"); }
    // 0 = no scroll container here, 1 = moved, 2 = reached its boundary.
    // Never fall through an exhausted episode list to an underlying page.
    static String scrollTarget = "";
    static int scrollAt(Object n, float x, float y, int action, int depth) throws Exception {
        if(n==null || depth>40 || !(Boolean)get(n,"isVisibleToUser")) return 0;
        String[] b=bounds(n).replace("[", "").replace("]", "").split(",");
        if(x<Integer.parseInt(b[0]) || x>=Integer.parseInt(b[2]) || y<Integer.parseInt(b[1]) || y>=Integer.parseInt(b[3])) return 0;
        String id=str(get(n,"getViewIdResourceName"));
        if(id.equals(PACKAGE+":id/l0") || id.equals(PACKAGE+":id/f8h")) {
            // ViewPager's action changes the selected page and dispatches its
            // playback callbacks. Scrolling its inner RecyclerView directly
            // can move items without changing the video, leaving a black page.
            scrollTarget=id;
            int actions=(Integer)get(n,"getActions");
            return (actions & action)!=0 && (Boolean)call(n,"performAction",new Class<?>[]{int.class},action) ? 1 : 2;
        }
        int count=(Integer)get(n,"getChildCount");
        for(int i=count-1;i>=0;i--) {
            Object child=call(n,"getChild",new Class<?>[]{int.class},i);
            if(child!=null) {
                try { int result=scrollAt(child,x,y,action,depth+1);if(result!=0) return result; }
                finally { get(child,"recycle"); }
            }
        }
        int actions=(Integer)get(n,"getActions");
        if((Boolean)get(n,"isScrollable") || (actions & (4096|8192))!=0) {
            scrollTarget = str(get(n,"getViewIdResourceName"))+" "+str(get(n,"getClassName"))+" "+bounds(n);
            return (actions & action)!=0 && (Boolean)call(n,"performAction",new Class<?>[]{int.class},action) ? 1 : 2;
        }
        return 0;
    }
    static String wheel(String[] args) throws Exception {
        int nx=Integer.parseInt(args[1]), ny=Integer.parseInt(args[2]), delta=Integer.parseInt(args[3]);
        if(nx<0 || nx>65535 || ny<0 || ny>65535 || (delta!=-120 && delta!=120))
            return "{\"ok\":false,\"reason\":\"invalid\"}";
        // Only act on visible nodes owned by PACKAGE. Accessibility actions
        // remain correctly scoped when another desktop/Android app has focus.
        // WSA places freeform windows on its virtual desktop; translate the
        // normalized native pointer into the app area, including its origin.
        List<Object> rs=roots();
        if(rs.isEmpty()) return "{\"ok\":false,\"reason\":\"no_window\"}";
        try {
            int[] area=null;
            for(Object root:rs) {
                String[] parts=bounds(root).replace("[", "").replace("]", "").split(",");
                int[] r=new int[4];for(int i=0;i<4;i++) r[i]=Integer.parseInt(parts[i]);
                if(area==null || (r[2]-r[0])*(r[3]-r[1])>(area[2]-area[0])*(area[3]-area[1])) area=r;
            }
            float x=area[0]+nx*(area[2]-area[0]-1)/65535f;
            float y=area[1]+ny*(area[3]-area[1]-1)/65535f;
            scrollTarget="";
            int result=scrollAt(rs.get(0),x,y,delta<0?4096:8192,0);
            return "{\"ok\":"+(result==1)+",\"edge\":"+(result==2)+",\"method\":\"scroll_action\",\"target\":"+quote(scrollTarget)+",\"x\":"+x+",\"y\":"+y+"}";
        } finally { for(Object root:rs) get(root,"recycle"); }
    }
    static String click(String[] a) throws Exception {
        // Resolve again, then compare the identity/text/bounds observed by Python.
        String[] path=a[1].split("/"); List<Object> rs=roots(); Object n=null;
        List<Object> held=new ArrayList<>(rs);
        try {
            int rootIndex=Integer.parseInt(path[0]);
            if(rootIndex>=rs.size()) return "{\"ok\":false,\"reason\":\"stale\"}";
            n=rs.get(rootIndex);
            for(int i=1;i<path.length && n!=null;i++) {
                n=call(n,"getChild",new Class<?>[]{int.class},Integer.parseInt(path[i]));
                if(n!=null) held.add(n);
            }
            if(n==null || !(Boolean)get(n,"isVisibleToUser") || !(Boolean)get(n,"isEnabled") ||
                !str(get(n,"getViewIdResourceName")).equals(decode(a[2])) ||
                !str(get(n,"getText")).equals(decode(a[3])) || !bounds(n).equals(a[4]))
                return "{\"ok\":false,\"reason\":\"stale\"}";
            if(a[0].equals("dismiss")) {
                // A quality tap can dismiss its own dialog. Do not send Back
                // unless its marker still resolves in the top Hongguo window.
                if(rootIndex!=0) return "{\"ok\":false,\"reason\":\"covered\"}";
                return back();
            }
            if(a[0].equals("validate")) {
                return "{\"ok\":"+(rootIndex==0)+"}";
            }
            if(a[0].equals("tap")) {
                Object w=get(n,"getWindow");
                if(w==null) return "{\"ok\":false}";
                int display=(Integer)get(w,"getDisplayId"); get(w,"recycle");
                String[] coords=a[4].substring(1,a[4].length()-1).split(",");
                float x=(Integer.parseInt(coords[0])+Integer.parseInt(coords[2]))/2f;
                float y=(Integer.parseInt(coords[1])+Integer.parseInt(coords[3]))/2f;
                String[] rb=bounds(rs.get(rootIndex)).replace("[", "").replace("]", "").split(",");
                if(x<Integer.parseInt(rb[0]) || x>=Integer.parseInt(rb[2]) || y<Integer.parseInt(rb[1]) || y>=Integer.parseInt(rb[3]))
                    return "{\"ok\":false,\"reason\":\"outside\"}";
                long now=(Long)cls("android.os.SystemClock").getMethod("uptimeMillis").invoke(null);
                boolean ok=true;
                for(int action=0;action<=1;action++) {
                    Object event=cls("android.view.MotionEvent").getMethod("obtain",long.class,long.class,int.class,float.class,float.class,int.class)
                        .invoke(null,now,now+action*40,action,x,y,0);
                    call(event,"setSource",new Class<?>[]{int.class},0x1002);
                    call(event,"setDisplayId",new Class<?>[]{int.class},display);
                    try { ok &= (Boolean)call(ui,"injectInputEvent",new Class<?>[]{cls("android.view.InputEvent"),boolean.class},event,true); }
                    finally { get(event,"recycle"); }
                }
                return "{\"ok\":"+ok+"}";
            }
            for(int i=0;i<12 && n!=null;i++) {
                if((Boolean)get(n,"isClickable")) {
                    boolean ok=(Boolean)call(n,"performAction",new Class<?>[]{int.class},16);
                    return "{\"ok\":"+ok+"}";
                }
                n=get(n,"getParent"); if(n!=null) held.add(n);
            }
            return "{\"ok\":false,\"reason\":\"not_clickable\"}";
        } finally { for(Object x:held) get(x,"recycle"); }
    }
    static String back() throws Exception {
        Object root=get(ui,"getRootInActiveWindow");
        if(root==null) return "{\"ok\":false}";
        try {
            if(!PACKAGE.equals(str(get(root,"getPackageName")))) return "{\"ok\":false}";
            return "{\"ok\":"+call(ui,"performGlobalAction",new Class<?>[]{int.class},1)+"}";
        } finally { get(root,"recycle"); }
    }
    public static void main(String[] args) throws Exception {
        Object thread=cls("android.os.HandlerThread").getConstructor(String.class).newInstance("HongguoUi");
        get(thread,"start"); Object looper=get(thread,"getLooper");
        Object conn=cls("android.app.UiAutomationConnection").getConstructor().newInstance();
        ui=cls("android.app.UiAutomation").getConstructor(cls("android.os.Looper"),cls("android.app.IUiAutomationConnection")).newInstance(looper,conn);
        call(ui,"connect",new Class<?>[]{int.class},1);
        Object info=get(ui,"getServiceInfo");
        Field flags=info.getClass().getField("flags");flags.setInt(info,flags.getInt(info)|0x10|0x40|0x2);
        info.getClass().getField("notificationTimeout").setLong(info,150);
        call(ui,"setServiceInfo",new Class<?>[]{info.getClass()},info);
        Class<?> listener=cls("android.app.UiAutomation$OnAccessibilityEventListener");
        Object proxy=Proxy.newProxyInstance(listener.getClassLoader(),new Class<?>[]{listener},(p,m,a)->{
            if(m.getName().equals("onAccessibilityEvent")) {
                Object e=a[0];
                if(PACKAGE.equals(str(get(e,"getPackageName"))) && ((Integer)get(e,"getEventType"))==32) {
                    String c=str(get(e,"getClassName"));if(c.contains("Activity")) activity=c;
                }
            }
            return null;
        });
        call(ui,"setOnAccessibilityEventListener",new Class<?>[]{listener},proxy);
        System.out.println("{\"ready\":true}");System.out.flush();
        try(BufferedReader reader=new BufferedReader(new InputStreamReader(System.in,"UTF-8"))) {
            String line;
            while((line=reader.readLine())!=null) {
                if(line.equals("quit")) break;
                try {
                    String[] a=line.split("\t",-1);
                    System.out.println(a[0].equals("snapshot") ? snapshot() : a[0].equals("back") ? back() : a[0].equals("wheel") && a.length==4 ? wheel(a) : (a[0].equals("click") || a[0].equals("tap") || a[0].equals("validate") || a[0].equals("dismiss")) && a.length==5 ? click(a) : "{\"ok\":false,\"reason\":\"unknown\"}");
                } catch(Exception e) { System.out.println("{\"ok\":false,\"error\":"+quote(e.toString())+"}"); }
                System.out.flush();
            }
        } finally {
            try { get(ui,"disconnect"); } finally { get(thread,"quitSafely"); }
        }
        System.exit(0);
    }
}
