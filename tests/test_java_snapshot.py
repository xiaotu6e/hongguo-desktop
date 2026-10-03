"""Exercise bridge root selection in a JVM with fake nodes, never Android/ADB."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import SkipTest, TestCase


PROJECT = Path(__file__).resolve().parents[1]
JAVA = Path(os.environ.get("JAVA_DIRECTORY", r"E:\tools\jdk-21.0.12.1+1")) / "bin"

RECT = """package android.graphics;
public class Rect { public int left, top, right, bottom; public Rect() {} }
"""

HARNESS = r"""
import java.util.*;
import java.util.Base64;
import android.graphics.Rect;

public class SnapshotHarness {
    public static class Node {
        final String name;
        final boolean visible;
        final List<Node> children=new ArrayList<>();
        boolean failText=false;
        int recycled=0, clicks=0, reads=0;
        Node(String name,boolean visible,Node... children) {
            this.name=name;this.visible=visible;this.children.addAll(Arrays.asList(children));
        }
        public String getPackageName() { return HongguoUi.PACKAGE; }
        public boolean isVisibleToUser() { reads++;return visible; }
        public String getViewIdResourceName() { return HongguoUi.PACKAGE+":id/"+name; }
        public String getText() { if(failText) throw new IllegalStateException("fake read failed");return name; }
        public String getContentDescription() { return ""; }
        public String getClassName() { return "fake.Node"; }
        public boolean isClickable() { return true; }
        public boolean isEnabled() { return true; }
        public boolean isSelected() { return false; }
        public boolean isChecked() { return false; }
        public void getBoundsInScreen(Rect rect) { rect.left=0;rect.top=0;rect.right=100;rect.bottom=200; }
        public int getChildCount() { return children.size(); }
        public Node getChild(int index) { return children.get(index); }
        public boolean performAction(int action) { clicks++;return action==16; }
        public void recycle() { recycled++; }
    }
    public static class Window {
        final Node node;
        Window(Node node) { this.node=node; }
        public Node getRoot() { return node; }
        public void recycle() {}
    }
    public static class Ui {
        final List<Window> windows=new ArrayList<>();
        List<Window> changed=null;
        int cacheClears=0;
        Ui(Node... nodes) { for(Node node:nodes) windows.add(new Window(node)); }
        public boolean clearCache() {
            cacheClears++;
            if(changed!=null) { windows.clear();windows.addAll(changed);changed=null; }
            return true;
        }
        public List<Window> getWindows() { return windows; }
        public Node getRootInActiveWindow() { return null; }
    }
    static String encode(String value) throws Exception {
        return Base64.getEncoder().encodeToString(value.getBytes("UTF-8"));
    }
    public static void main(String[] args) throws Exception {
        Node empty=new Node("empty",false), child=new Node("child",true);
        Node primary=new Node("primary",true,child), behind=new Node("behind",true);
        behind.failText=true; // A snapshot must never traverse this covered tree.
        HongguoUi.ui=new Ui(empty,primary,behind);
        System.out.println(HongguoUi.snapshot());
        System.out.println(HongguoUi.click(new String[]{"click","1/0",encode(child.getViewIdResourceName()),
            encode(child.name),"[0,0,100,200]"}));
        System.out.println("{\"clicks\":"+child.clicks+",\"behindReads\":"+behind.reads+
            ",\"rootRecycles\":["+empty.recycled+","+primary.recycled+","+behind.recycled+"]}");

        Node a=new Node("a",false), b=new Node("b",false);
        HongguoUi.ui=new Ui(a,b);
        System.out.println(HongguoUi.snapshot());
        System.out.println("{\"rootRecycles\":["+a.recycled+","+b.recycled+"]}");

        Node bad=new Node("bad",true), remaining=new Node("remaining",true);
        bad.failText=true;
        HongguoUi.ui=new Ui(bad,remaining);
        boolean failed=false;
        try { HongguoUi.snapshot(); } catch(Exception expected) { failed=true; }
        System.out.println("{\"failed\":"+failed+",\"remainingReads\":"+remaining.reads+
            ",\"rootRecycles\":["+bad.recycled+","+remaining.recycled+"]}");

        Node stale=new Node("portrait",true), rotated=new Node("landscape",true);
        Ui changing=new Ui(stale);
        changing.changed=Arrays.asList(new Window(rotated));
        HongguoUi.ui=changing;
        System.out.println(HongguoUi.snapshot());
        changing.changed=Arrays.asList(new Window(stale));
        System.out.println(HongguoUi.click(new String[]{"validate","0",encode(rotated.getViewIdResourceName()),
            encode(rotated.name),"[0,0,100,200]"}));
        System.out.println("{\"cacheClears\":"+changing.cacheClears+",\"clicks\":"+(stale.clicks+rotated.clicks)+"}");
    }
}
"""


class JavaSnapshotTests(TestCase):
    @classmethod
    def setUpClass(cls):
        if not (JAVA / "javac.exe").is_file() or not (JAVA / "java.exe").is_file():
            raise SkipTest("Local JDK required for the offline bridge compatibility test")
        cls.temp = tempfile.TemporaryDirectory(prefix="hongguo-fake-nodes-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        rect = directory / "Rect.java"
        harness = directory / "SnapshotHarness.java"
        rect.write_text(RECT, encoding="utf-8")
        harness.write_text(HARNESS, encoding="utf-8")
        compile_result = subprocess.run([
            str(JAVA / "javac.exe"), "--release", "8", "-encoding", "UTF-8", "-d", str(directory),
            str(PROJECT / "android-bridge/HongguoUi.java"), str(rect), str(harness),
        ], capture_output=True, text=True, timeout=30)
        if compile_result.returncode:
            raise AssertionError(compile_result.stderr)
        result = subprocess.run([str(JAVA / "java.exe"), "-cp", str(directory), "SnapshotHarness"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.results = [json.loads(line) for line in result.stdout.splitlines()]

    def test_empty_top_root_does_not_hide_player_or_change_action_paths(self):
        snapshot, action, state = self.results[:3]
        self.assertEqual([node["path"] for node in snapshot["nodes"]], ["1", "1/0"])
        self.assertIsNone(snapshot["playing"])
        self.assertEqual(snapshot["diagnostics"]["rootCount"], 3)
        self.assertEqual(snapshot["diagnostics"]["rootIndex"], 1)
        self.assertEqual(snapshot["diagnostics"]["visited"], 3)
        self.assertGreaterEqual(snapshot["diagnostics"]["snapshot_ms"], 0)
        self.assertTrue(action["ok"])
        self.assertEqual(state["clicks"], 1)
        self.assertEqual(state["behindReads"], 0)
        self.assertEqual(state["rootRecycles"], [2, 2, 2])

    def test_all_empty_roots_return_empty_snapshot_and_release_all_nodes(self):
        snapshot, state = self.results[3:5]
        self.assertEqual(snapshot["nodes"], [])
        self.assertEqual(snapshot["diagnostics"]["rootIndex"], -1)
        self.assertEqual(snapshot["diagnostics"]["visited"], 2)
        self.assertEqual(state["rootRecycles"], [1, 1])

    def test_tree_read_failure_recycles_even_the_roots_not_yet_walked(self):
        state = self.results[5]
        self.assertTrue(state["failed"])
        self.assertEqual(state["remainingReads"], 0)
        self.assertEqual(state["rootRecycles"], [1, 1])

    def test_rotated_window_refreshes_cache_and_validation_rejects_old_target(self):
        snapshot, action, state = self.results[6:9]
        self.assertEqual(snapshot["nodes"][0]["text"], "landscape")
        self.assertFalse(action["ok"])
        self.assertEqual(action["reason"], "stale")
        self.assertEqual(state["cacheClears"], 2)
        self.assertEqual(state["clicks"], 0)
