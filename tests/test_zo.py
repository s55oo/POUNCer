"""ZO priorities, stable index mapping, live configuration, and canvas reuse."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest

from pouncer.urgency import classify
from pouncer.zo import layout, neighbor
from pouncer.config import Config, load_config
from pouncer import settings


class PolicyTests(unittest.TestCase):
    def test_run_boundaries(self):
        for run, expected in [(0,'urgent'), (29.9,'urgent'), (30,'medium'),
                              (59.9,'medium'), (60,'normal'), (100,'normal')]:
            self.assertEqual(classify({'run':run}, 'UNKNOWN')['urgency'], expected)

    def test_hours_and_combined_evidence(self):
        for hours, expected in [(0.24,'urgent'), (.25,'medium'), (.49,'medium'),
                                (.5,'normal'), (1,'normal')]:
            info = {'hours':hours, 'hours_unrounded':True, 'history_contests':['TTC-SPCWC']}
            self.assertEqual(classify(info, 'TTC-SPCWC')['urgency'], expected)
        self.assertEqual(classify({'run':80,'hours':.2,'hours_unrounded':True,'history_contests':['TTC-SPCWC']},
                                  'TTC-SPCWC')['urgency'], 'urgent')
        for hours, expected in [(5.9,'urgent'), (6,'medium'), (11.9,'medium'), (12,'normal')]:
            self.assertEqual(classify({'hours':hours,'hours_unrounded':True,'history_contests':['CQWW']},
                                      'CQWWCW')['urgency'], expected)

    def test_unknown_provenance_invalid_and_confidence(self):
        for info in ({}, {'run':float('nan')}, {'run':float('inf')}, {'run':True},
                     {'run':-1}, {'run':101}, {'hours':.1},
                     {'hours':.1,'history_contests':['CQWW']}):
            self.assertEqual(classify(info, 'TTC-SPCWC')['urgency'], 'unknown')
        x = classify({'run':20, 'history_contests':['CQWW'], 'rec':True}, 'TTC-SPCWC')
        self.assertEqual(x['urgency'], 'urgent')
        self.assertEqual(x['urgency_confidence'], 'limited')
        self.assertTrue(x['urgency_reasons'])
        self.assertEqual(classify({'hours':.3,'hours_unrounded':True,'history_contests':['CUSTOM']}, 'CUSTOM',60)['urgency'], 'medium')


class LayoutTests(unittest.TestCase):
    def test_three_columns_counts_unknown_and_original_indexes(self):
        items = [{'urgency':g} for g in ('normal','urgent','unknown','medium','urgent','normal')]
        boxes, headings, height = layout(items, 960, 100)
        self.assertEqual([b[1] for b in boxes], list(range(6)))
        self.assertLess(boxes[1][0][0], boxes[3][0][0])
        self.assertLess(boxes[3][0][0], boxes[0][0][0])
        self.assertEqual(boxes[0][0][0], boxes[2][0][0])
        self.assertGreater(boxes[2][0][1], boxes[5][0][3])
        self.assertEqual([h[2] for h in headings], ['⚡ Urgent (2)','Medium (1)','Normal (3)','Unknown (1)'])
        self.assertGreaterEqual(height, max(b[0][3] for b in boxes))
        self.assertEqual(len(layout([], 320, 132)[1]),3)

    def test_arrows_follow_columns_with_unequal_lengths(self):
        items = [{'urgency':g} for g in ('normal','urgent','unknown','medium','urgent','normal')]
        boxes, _, _ = layout(items, 960, 100)
        self.assertEqual(neighbor(boxes,None,0,1),1)
        self.assertEqual(neighbor(boxes,1,0,1),4)
        self.assertEqual(neighbor(boxes,4,0,-1),1)
        self.assertEqual(neighbor(boxes,4,1,0),3)
        self.assertEqual(neighbor(boxes,3,1,0),0)
        self.assertEqual(neighbor(boxes,5,0,1),2)
        self.assertEqual(neighbor(boxes,2,0,1),2)

    def test_live_setting_persistence_and_original_view_restored(self):
        from tests.test_so2r import _import_app
        app = _import_app()
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'config.ini')
            Path(path).write_text('[ui]\nspot_tiles = false\n')
            cfg=load_config(path)
            self.assertFalse(cfg.zo_mode)
            f=types.SimpleNamespace(cfg=cfg, tiles_var=types.SimpleNamespace(get=lambda:cfg.tiles),
                compact_var=types.SimpleNamespace(get=lambda:False), rows=object(), _clean=lambda:True)
            self.assertEqual(app.PouncerApp._list_kind(f),'rows')
            result=settings.save(cfg,path,'ui.zo_mode','true')
            self.assertTrue(result['ok']); self.assertTrue(result['live'])
            self.assertTrue(load_config(path).zo_mode)
            self.assertEqual(app.PouncerApp._list_kind(f),'tiles')
            self.assertFalse(cfg.tiles)
            settings.save(cfg,path,'ui.zo_mode','false')
            self.assertEqual(app.PouncerApp._list_kind(f),'rows')
            cfg.tiles=True; cfg.zo_mode=True
            self.assertEqual(app.PouncerApp._list_kind(f),'tiles')
            cfg.zo_mode=False
            self.assertEqual(app.PouncerApp._list_kind(f),'tiles')


    def test_view_menu_toggle_saves_and_shows(self):
        """View > ZO mode / Ctrl+Z: flips, saves [ui] zo_mode, shows the tiles."""
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'config.ini')
            Path(path).write_text('[ui]\nspot_tiles = false\n')
            cfg = load_config(path)
            var = types.SimpleNamespace(v=False)
            var.get = lambda: var.v
            var.set = lambda x: setattr(var, 'v', x)
            shown = []
            f = types.SimpleNamespace(cfg=cfg, zo_var=var, _dirty=False,
                                      _show_tiles=lambda: shown.append(cfg.zo_mode),
                                      _save_setting=lambda k, v: settings.save(cfg, path, k, v))
            P._toggle_zo(f, flip=True)                  # Ctrl+Z
            self.assertTrue(cfg.zo_mode)
            self.assertTrue(load_config(path).zo_mode)
            self.assertTrue(f._zo_mode_seen)
            var.set(False)                              # the menu tick flips it first
            P._toggle_zo(f, flip=False)
            self.assertFalse(load_config(path).zo_mode)
            self.assertEqual(shown, [True, False])

    def test_tick_applies_live_layout_on_main_thread_once_per_change(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        class StopTick(Exception):
            pass
        def stop():
            raise StopTick
        calls=[]
        f=types.SimpleNamespace(cfg=types.SimpleNamespace(zo_mode=False),
            updater=types.SimpleNamespace(maybe_check=stop),
            _show_tiles=lambda:calls.append(f.cfg.zo_mode))
        for enabled in (False, True, True, False):
            f.cfg.zo_mode=enabled
            with self.assertRaises(StopTick):
                P._tick_body(f)
        self.assertEqual(calls,[True,False])


class Canvas:
    def __init__(self):
        self.items={}; self.top=0; self.width=960; self.height=300
    def create_polygon(self,*a,**kw): return self._create(kw)
    def create_rectangle(self,*a,**kw): return self._create(kw)
    def create_text(self,*a,**kw): return self._create(kw)
    def _create(self,kw):
        i=len(self.items)+1; self.items[i]=dict(kw); return i
    def coords(self,i,*args): self.items[i]['coords']=args
    def itemconfigure(self,i,**kw): self.items[i].update(kw)
    def bbox(self,i):
        item=self.items[i]; x,y=item.get('coords',(0,0))[:2]
        return x,y,x+len(item.get('text',''))*6,y+12
    def configure(self,**kw): pass
    def winfo_width(self): return self.width
    def winfo_height(self): return self.height
    def canvasy(self,y): return self.top+y
    def canvasx(self,x): return x
    def focus_set(self): pass
    def yview_moveto(self,fraction): pass


class CanvasTests(unittest.TestCase):
    def make(self):
        from tests.test_so2r import _import_app
        _import_app()
        from pouncer.tiles import TileList
        t=object.__new__(TileList)
        t.c=Canvas(); t.zo_mode=True; t._zo_headings=[]; t._visible_items=()
        t._pool=[]; t._sig=None; t.selected=None; t._boxes=[]; t._tip=None; t._tip_at=None
        t._drawn=(0,0); t._height=0; t.on_select=None
        t.on_tap=lambda i:self.taps.append(i)
        t._sb=types.SimpleNamespace(set=lambda *a:None)
        self.taps=[]
        return t

    def test_click_mapping_and_bounded_reuse_across_refresh_and_scroll(self):
        t=self.make()
        items=[{'call':f'CALL{i}','urgency':('urgent','medium','normal','unknown')[i%4],
                'line1':'7020.0 R40% ~12h','line2':'1200Z 22dB cq3m'} for i in range(3000)]
        t.set_items(items)
        self.assertLess(len(t._pool),100)
        initial=len(t.c.items)
        for n in range(30):
            t.set_items([dict(it,line2=f'1200Z 22dB cq{n}m') for it in items])
        self.assertEqual(len(t.c.items),initial)
        t.c.top=20000; t.draw(force=True)
        self.assertLess(len(t._pool),100)
        current=len(t.c.items)
        for _ in range(10): t.draw(force=True)
        self.assertEqual(len(t.c.items),current)
        box,index=next(b for b in t._boxes if t.c.top <= b[0][1] < t.c.top+300)
        t._click(types.SimpleNamespace(x=box[0]+5,y=box[1]-t.c.top+5))
        self.assertEqual(self.taps,[index])
        t._tap_selected(); self.assertEqual(self.taps,[index,index])
        t._arrow(0,1)
        self.assertEqual(t.items[t.selected]['urgency'],items[index]['urgency'])
        t.zo_mode=False; t.c.top=0; t.draw(force=True)
        self.assertTrue(all(t.c.items[i]['state']=='hidden' for i in t._zo_headings))
        t.zo_mode=True; t.draw(force=True)
        self.assertEqual(len(t._zo_headings),4)


class BrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is needed for browser tests')
    def test_browser_zo(self):
        r=subprocess.run([shutil.which('node'),'tests/browser_zo.cjs'],cwd=Path(__file__).resolve().parents[1],
                         capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
