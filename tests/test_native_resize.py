"""Compile the actual native sizing math and test its public interaction rules."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEFT, RIGHT, TOP, BOTTOM = 1, 2, 4, 8


class NativeResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vswhere = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
        if not vswhere.is_file():
            raise unittest.SkipTest('Visual C++ build tools required for native resize tests.')
        result = subprocess.run([str(vswhere), '-latest', '-products', '*', '-requires',
                                 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                                capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            raise unittest.SkipTest('Visual C++ build tools required for native resize tests.')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        cls.executable = directory / 'resize-tests.exe'
        environment = Path(result.stdout.strip()) / 'VC/Auxiliary/Build/vcvars64.bat'
        command = directory / 'compile.cmd'
        command.write_text(
            f'@call "{environment}" >nul\n@cl /nologo /EHsc /std:c++17 /DNOMINMAX '
            f'/Fo"{directory / "test.obj"}" /Fe"{cls.executable}" '
            f'"{ROOT / "tests/native_resize_driver.cpp"}"\n@exit /b %errorlevel%\n', encoding='mbcs')
        result = subprocess.run([os.environ['ComSpec'], '/d', '/c', str(command)], capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('mbcs', errors='replace') + result.stderr.decode('mbcs', errors='replace'))

    def resize(self, start, edge, dx, dy, work=(0, 0, 3840, 2090), minimum=270):
        output = subprocess.check_output([str(self.executable), 'resize', *map(str, (*start, *work, edge, dx, dy, minimum))], text=True)
        return tuple(map(int, output.split()))

    def hit(self, x, y):
        return int(subprocess.check_output([str(self.executable), 'hit', '100', '100', '1060', '640', str(x), str(y), '9', '18'], text=True))

    def test_all_edges_and_corners_are_accessible_while_video_interior_is_not(self):
        for x,y,edge in [(100,370,LEFT),(1059,370,RIGHT),(580,100,TOP),(580,639,BOTTOM),
                         (110,110,LEFT|TOP),(1050,110,RIGHT|TOP),
                         (110,630,LEFT|BOTTOM),(1050,630,RIGHT|BOTTOM),
                         (580,370,0),(99,370,0),(1060,370,0)]:
            with self.subTest(x=x,y=y): self.assertEqual(self.hit(x,y),edge)

    def test_landscape_edges_and_corners_keep_16_by_9_when_enlarged_or_shrunk(self):
        for edge in [LEFT,RIGHT,TOP,BOTTOM,LEFT|TOP,RIGHT|TOP,LEFT|BOTTOM,RIGHT|BOTTOM]:
            for delta in [-170,180]:
                r=self.resize((800,500,1760,1040),edge,delta,delta)
                self.assertLessEqual(abs((r[2]-r[0])-(r[3]-r[1])*16/9),.51)
                self.assertGreaterEqual(r[3]-r[1],270)

    def test_portrait_edges_and_corners_keep_9_by_16(self):
        for edge in [LEFT,RIGHT,TOP,BOTTOM,LEFT|TOP,RIGHT|TOP,LEFT|BOTTOM,RIGHT|BOTTOM]:
            for delta in [-170,180]:
                r=self.resize((800,200,1340,1160),edge,delta,delta)
                self.assertLessEqual(abs((r[2]-r[0])-(r[3]-r[1])*9/16),.51)
                self.assertGreaterEqual(r[2]-r[0],270)

    def test_dragging_a_side_keeps_opposite_side_and_cross_axis_center(self):
        start=(800,500,1760,1040)
        for edge in [LEFT,RIGHT,TOP,BOTTOM]:
            r=self.resize(start,edge,180,180)
            if edge==LEFT: self.assertEqual(r[2],start[2])
            if edge==RIGHT: self.assertEqual(r[0],start[0])
            if edge==TOP: self.assertEqual(r[3],start[3])
            if edge==BOTTOM: self.assertEqual(r[1],start[1])
            axis=(1,3) if edge in (LEFT,RIGHT) else (0,2)
            self.assertLessEqual(abs(r[axis[0]]+r[axis[1]]-start[axis[0]]-start[axis[1]]),1)

    def test_dragging_a_corner_keeps_opposite_corner(self):
        start=(800,500,1760,1040)
        for edge in [LEFT|TOP,RIGHT|TOP,LEFT|BOTTOM,RIGHT|BOTTOM]:
            r=self.resize(start,edge,180,180)
            self.assertEqual(r[2] if edge&LEFT else r[0],start[2] if edge&LEFT else start[0])
            self.assertEqual(r[3] if edge&TOP else r[1],start[3] if edge&TOP else start[1])

    def test_large_drag_stays_inside_the_monitor_and_keeps_ratio(self):
        for start,ratio in [((800,500,1760,1040),16/9),((800,200,1340,1160),9/16)]:
            for edge in [LEFT,RIGHT,TOP,BOTTOM,LEFT|TOP,RIGHT|TOP,LEFT|BOTTOM,RIGHT|BOTTOM]:
                for delta in [-10000,10000]:
                    r=self.resize(start,edge,delta,delta)
                    self.assertGreaterEqual(r[0],0); self.assertGreaterEqual(r[1],0)
                    self.assertLessEqual(r[2],3840); self.assertLessEqual(r[3],2090)
                    self.assertLessEqual(abs((r[2]-r[0])-(r[3]-r[1])*ratio),.51)

    def test_secondary_monitor_negative_coordinates_are_supported(self):
        r=self.resize((-1800,1000,-1260,1960),LEFT|BOTTOM,-200,200,work=(-2160,0,0,3840))
        self.assertEqual(r[2],-1260); self.assertEqual(r[1],1000)
        self.assertGreaterEqual(r[0],-2160)
        self.assertLessEqual(abs((r[2]-r[0])-(r[3]-r[1])*9/16),.51)

    def test_minimum_size_and_no_resize_gesture_do_not_invert_the_window(self):
        start=(800,500,1760,1040)
        r=self.resize(start,RIGHT|BOTTOM,-10000,-10000)
        self.assertEqual((r[2]-r[0],r[3]-r[1]),(480,270))
        self.assertEqual(self.resize(start,0,500,500),start)


if __name__=='__main__': unittest.main()
