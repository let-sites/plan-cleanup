"""Plan Cleanup main window."""
from __future__ import annotations
import os, sys, math, traceback, tempfile
from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QThread, Signal, QObject, QPointF, QRectF, QTimer, QSize
from PySide6.QtGui import QFont, QAction, QColor, QPixmap, QImage, QIcon, QKeySequence, QTransform
from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog, QListWidget, QListWidgetItem, QDockWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QInputDialog, QProgressDialog, QCheckBox, QToolBar, QLineEdit, QFormLayout, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QAbstractItemView, QSplitter, QSizePolicy, QListView, QProgressBar)

from planclean import __version__
from planclean.profile import Profile, Settings, safe_name
from planclean.symbols import SYMBOL_TYPES, PEN_ROLES, label as sym_label
from planclean.engine import clean_pdf, pens_in, guess_roles
from planclean.teach import Teacher
from planclean.vec import in_region, pen_key
from planclean import oda, dxfout, dwgin
from planclean.result import Result, turned
from planclean.jobs import CleanJob, attach_to_page
from planclean.plan import Plan, combine, reattach, find_board, on_board, LEVELS, level_rank
from planclean.preview import BlockShapes
from .view import SheetView, CLASS_COLORS
from .sources import PdfSheet, DwgSheet
from .start import StartDialog, _big, DropOverlay, _plan_url, _plan_urls, BuilderSettingsDialog, OpenPlanDialog, BuildersPage, edit_builder_settings
THUMB_W = 200
AREA_TYPES = ['Electrical plan', 'Option', 'Exterior elevation', 'Interior elevation']
is_elev = lambda t: 'elevation' in t

SCALES = [('1/4" = 1\'-0"', 48), ('1/8" = 1\'-0"', 96), ('3/16" = 1\'-0"', 64), ('3/8" = 1\'-0"', 32), ('1/2" = 1\'-0"', 24)]


def upright(res, item):
    """An elevation on a sideways sheet comes out of the cleanup sideways: turn it the way it reads on screen."""
    if item.kind != 'pdf': return res
    try:
        with pymupdf.open(item.file) as d: rot = d[item.page].rotation
    except Exception: return res
    return turned(res, -rot)

class Worker(QObject):
    done = Signal(object); failed = Signal(str); progress = Signal(str)
    def __init__(self, fn): super().__init__(); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn(self.progress.emit))
        except Exception: self.failed.emit(traceback.format_exc())

def run_in_thread(parent, fn, on_done, title):
    dlg = QProgressDialog(title, None, 0, 0, parent); dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0); dlg.show()
    th = QThread(parent); w = Worker(fn); w.moveToThread(th)
    th.started.connect(w.run)
    w.progress.connect(dlg.setLabelText)
    def fin(res): dlg.close(); th.quit(); on_done(res)
    def err(msg): dlg.close(); th.quit(); QMessageBox.critical(parent, 'Something went wrong', msg[-2000:])
    w.done.connect(fin); w.failed.connect(err)
    parent._threads = getattr(parent, '_threads', []) + [(th, w)]
    th.start()

class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent); self.setWindowTitle('Settings'); self.s = settings
        outer = QVBoxLayout(self); outer.setContentsMargins(20, 16, 20, 14)
        tabs = QTabWidget(); outer.addWidget(tabs, 1)

        # -- Files and text
        g = QWidget(); f = QFormLayout(g); f.setContentsMargins(16, 16, 16, 16); f.setVerticalSpacing(14); f.setHorizontalSpacing(14)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter); f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.tpl = QLineEdit(settings.master_template); b4 = QPushButton('Choose…'); b4.clicked.connect(lambda: self._file(self.tpl, 'Drawings (*.dwg *.dxf)'))
        self.lib = QLineEdit(settings.block_library); b2 = QPushButton('Choose…'); b2.clicked.connect(lambda: self._file(self.lib, 'Drawings (*.dwg *.dxf)'))
        self.prof = QLineEdit(settings.profiles_folder); b1 = QPushButton('Choose…'); b1.clicked.connect(lambda: self._dir(self.prof))
        self.oda = QLineEdit(settings.oda_path or (oda.find_converter() or '')); b3 = QPushButton('Choose…'); b3.clicked.connect(lambda: self._file(self.oda, 'Programs (*)'))
        for lab, ed, b, tip in [('Master template', self.tpl, b4, 'Your MASTER FILE TEMPLATE (DWG). Plans and options are placed on it when you export. Empty: a blank drawing.'),
                                ('Block library', self.lib, b2, 'A drawing (DWG or DXF) that holds your standard blocks, e.g. one of your plan files.'),
                                ('Builders folder', self.prof, b1, 'Put this in a shared folder (OneDrive, Dropbox) so everyone uses the same builder setups.'),
                                ('ODA File Converter', self.oda, b3, 'Free from opendesign.com. Needed to read and write DWG.')]:
            ed.setMinimumWidth(480); ed.setMinimumHeight(30); ed.setToolTip(tip); ed.setCursorPosition(0)
            ed.textChanged.connect(lambda t, e=ed: e.setToolTip(t))
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.addWidget(ed, 1); row.addWidget(b)
            w = QWidget(); w.setLayout(row)
            col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(2); col.addWidget(w)
            hint = QLabel(tip); hint.setProperty('kind', 'muted'); hint.setWordWrap(True); col.addWidget(hint)
            box = QWidget(); box.setLayout(col)
            l = QLabel(f'<b>{lab}</b>'); f.addRow(l, box)
        self.th_room = QDoubleSpinBox(); self.th_room.setValue(settings.text_height_room); self.th_room.setSuffix(' in'); self.th_room.setMaximumWidth(120)
        self.th_note = QDoubleSpinBox(); self.th_note.setValue(settings.text_height_note); self.th_note.setSuffix(' in'); self.th_note.setMaximumWidth(120)
        f.addRow(QLabel('<b>Room name text height</b>'), self.th_room); f.addRow(QLabel('<b>Note text height</b>'), self.th_note)
        tabs.addTab(g, 'Files and text')

        # -- Builders
        self.builders = BuildersPage(settings, self, on_change=(parent.builders_changed if parent is not None else None))
        bw = QWidget(); bv = QVBoxLayout(bw); bv.setContentsMargins(16, 12, 16, 12); bv.addWidget(self.builders)
        tabs.addTab(bw, 'Builders')

        # -- Blocks
        bl = QWidget(); bv = QVBoxLayout(bl); bv.setContentsMargins(16, 12, 16, 12)
        bv.addWidget(QLabel('Which of your blocks is used for each symbol type:'))
        self.table = QTableWidget(len(SYMBOL_TYPES) - 1, 2); self.table.setHorizontalHeaderLabels(['Symbol type', 'Your block'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.table.verticalHeader().hide()
        blocks = parent.library_blocks() if parent else []
        self.combos = {}
        for i, (k, (lab, default, _)) in enumerate([kv for kv in SYMBOL_TYPES.items() if kv[0] != 'remove']):
            self.table.setItem(i, 0, QTableWidgetItem(lab))
            cb = QComboBox(); cb.setEditable(True); cb.addItems(sorted(set(blocks + [settings.block_for(k)])))
            cb.setCurrentText(settings.block_for(k)); self.table.setCellWidget(i, 1, cb); self.combos[k] = cb
        bv.addWidget(self.table, 1)
        tabs.addTab(bl, 'Symbol blocks')
        if parent is not None and getattr(parent, '_settings_tab', None) is not None: tabs.setCurrentIndex(parent._settings_tab)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.resize(860, 620)
    def _dir(self, ed):
        d = QFileDialog.getExistingDirectory(self, 'Choose folder', ed.text())
        if d: ed.setText(d)
    def _file(self, ed, flt):
        f, _ = QFileDialog.getOpenFileName(self, 'Choose file', ed.text(), flt)
        if f: ed.setText(f)
    def apply(self):
        s = self.s; s.profiles_folder = self.prof.text(); s.block_library = self.lib.text(); s.oda_path = self.oda.text()
        s.text_height_room = self.th_room.value(); s.text_height_note = self.th_note.value(); s.master_template = self.tpl.text()
        s.block_map = {k: cb.currentText() for k, cb in self.combos.items() if cb.currentText() != SYMBOL_TYPES[k][1]}
        s.save()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Plan Cleanup {__version__}')
        self.settings = Settings.load()
        self.sheet = None; self.sheets = []; self.region = None; self.profile = None
        self.teacher = None; self.result: Result | None = None; self.dwg_doc = None; self._lib = None
        self.view = SheetView(); self.setCentralWidget(self.view)
        self.view.regionDrawn.connect(self.on_region); self.view.clicked.connect(self.on_teach_click); self.view.itemPicked.connect(self.on_pick)
        self._toolbar(); self._docks(); self._menus()
        # big on/off switch for the builder's original drawing, pinned to the corner of the plan
        self.orig_btn = QPushButton('Show original  (O)'); self.orig_btn.setCheckable(True); self.orig_btn.setObjectName('origbtn')
        self.orig_btn.setToolTip('Flip the builder\'s original drawing on and off underneath the cleaned plan (key: O)')
        self.orig_btn.setCursor(Qt.PointingHandCursor)
        self.orig_btn.setStyleSheet('#origbtn{background:white;border:1px solid #c9d3db;border-radius:8px;padding:8px 16px;'
                                    'font-size:14px;font-weight:600;color:#1f2a33}'
                                    '#origbtn:hover{background:#edf3f7}'
                                    '#origbtn:checked{background:#4a90b8;border-color:#4a90b8;color:white}')
        self.orig_btn.toggled.connect(self._orig_toggled); self.view.set_corner_widget(self.orig_btn); self.orig_btn.hide()
        a = QAction(self); a.setShortcut(QKeySequence('O')); a.triggered.connect(lambda: self.orig_btn.isVisible() and self.orig_btn.toggle())
        self.addAction(a)
        self.setAcceptDrops(True); self._overlay = DropOverlay(self)
        self.resize(1500, 950)
        self._undo = []
        self.file_name = ''; self.plan: Plan | None = None; self.editing = None   # editing: index of the plan area being redone
        self.statusBar().showMessage('Click New plan to start.')

    # ---------- layout
    def _toolbar(self):
        tb = QToolBar('Main'); tb.setMovable(False); self.addToolBar(tb)
        # only before a plan is open; once in a plan they're in the File menu
        self.new_btn = _big('New plan…', primary=True); self.new_btn.clicked.connect(self.new_cleanup); self.new_act_tb = tb.addWidget(self.new_btn)
        self.open_btn = _big('Open plan…'); self.open_btn.clicked.connect(self.open_plan_dialog); self.open_act_tb = tb.addWidget(self.open_btn)
        self.info = QLabel(''); self.info.setStyleSheet('font-size:14px;padding-left:12px'); tb.addWidget(self.info)
        self.names_btn = QPushButton('Plan Names'); self.names_btn.setCursor(Qt.PointingHandCursor)
        self.names_btn.setToolTip('Plan name, community and elevation names (they fill the template\'s labels)')
        self.names_btn.setStyleSheet('QPushButton{border:1px solid #c9d3db;border-radius:12px;padding:3px 12px;background:white;font-size:13px}'
                                     'QPushButton:hover{background:#edf3f7}')
        self.names_btn.clicked.connect(self.edit_names); self.names_act_tb = tb.addWidget(self.names_btn); self.names_act_tb.setVisible(False)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); tb.addWidget(spacer)
        self.save_btn = _big('Export DWG', primary=True); self.save_btn.clicked.connect(self.save_plan_dwg); self.save_btn.setEnabled(False)
        self.save_btn.setToolTip('The last step: plans and options go on your master template, elevations in their own drawing')
        tb.addWidget(self.save_btn)
        b = _big('Settings'); b.clicked.connect(self.open_settings); tb.addWidget(b)

    def _menus(self):
        """A normal menu bar (on a Mac it sits at the top of the screen)."""
        mb = self.menuBar()
        def act(menu, text, fn, key=None, role=None, tip=''):
            a = QAction(text, self); a.triggered.connect(lambda *_: fn())
            if key: a.setShortcut(QKeySequence(key))
            if role is not None: a.setMenuRole(role)
            if tip: a.setStatusTip(tip)
            menu.addAction(a); return a
        m = mb.addMenu('File')
        act(m, 'New plan…', self.new_cleanup, QKeySequence.New)
        act(m, 'Open plan…', self.open_plan_dialog, QKeySequence.Open)
        self.recent_menu = m.addMenu('Open recent'); self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        act(m, 'Add files to this plan…', self.add_files)
        act(m, 'Plan names…', self.edit_names)
        m.addSeparator()
        self.save_act = act(m, 'Export DWG', self.save_plan_dwg, QKeySequence.Save, tip='Last step: write the plan into your drawings')
        m.addSeparator()
        act(m, 'Settings…', self.open_settings, QKeySequence.Preferences, QAction.PreferencesRole)
        act(m, 'Quit', self.close, QKeySequence.Quit, QAction.QuitRole)
        m = mb.addMenu('Edit')
        act(m, 'Undo', self.undo, QKeySequence.Undo, tip='Undo the last change in the review (Looks right, a symbol type, a delete)')
        m = mb.addMenu('View')
        act(m, 'Fit to window', self.view.fit, 'F')
        act(m, 'Zoom in', lambda: self.view.zoom(1.25), QKeySequence.ZoomIn)
        act(m, 'Zoom out', lambda: self.view.zoom(0.8), QKeySequence.ZoomOut)
        act(m, 'Show original on/off', lambda: self.orig_btn.isVisible() and self.orig_btn.toggle())
        m = mb.addMenu('Builders')
        act(m, 'Builders and what they show…', self.manage_builders, tip='See every builder; change what to show, rename, archive or delete')
        act(m, 'Teach this builder\'s symbols…', self.open_teach)

    def _fill_recent(self):
        self.recent_menu.clear()
        saved = Plan.list_saved()[:10]
        if not saved: a = self.recent_menu.addAction('(no saved plans yet)'); a.setEnabled(False); return
        for name, folder, _ in saved:
            a = self.recent_menu.addAction(name); a.triggered.connect(lambda *_, f=folder: self.open_plan(f))

    def manage_builders(self):
        self._settings_tab = 1
        try: self.open_settings()
        finally: self._settings_tab = None

    def builders_changed(self, name=None, old=None):
        """A builder was edited, renamed, archived or deleted in Settings."""
        if not self.profile: return
        cur = safe_name(self.profile.name)
        if old and old == cur:
            self.set_builder(name if name else None)
            if self.plan: self.plan.builder = name or ''; self.plan.save()
        elif name and name == cur: self.set_builder(name)

    def current_builder_settings(self):
        if not self.profile:
            QMessageBox.information(self, 'Builder settings', 'No builder is chosen yet. Start or open a plan first, or use Builders ▸ Manage builders….'); return
        edit_builder_settings(self.settings, self.profile.name, self)
        self.set_builder(self.profile.name)

    def update_info(self):
        parts = []
        if self.profile: parts.append(f'<b>Builder:</b> {self.profile.name}')
        if self.plan: parts.append(f'<b>Plan:</b> {self.plan.name}')
        if self.sheet: parts.append(f'<b>Sheet:</b> {getattr(self.sheet, "label", self.sheet.title)}')
        self.info.setText('&nbsp;&nbsp;&nbsp;'.join(parts))

    def _docks(self):
        self.pages = QListWidget(); self.pages.setObjectName('sheets'); self.pages.setFixedWidth(190)
        self.pages.currentRowChanged.connect(self.on_page)
        side = QWidget(); sv = QVBoxLayout(side); sv.setContentsMargins(10, 10, 6, 10)
        hdr = QLabel('<b>Sheets</b>'); hdr.setStyleSheet('font-size:15px'); sv.addWidget(hdr)
        tip = QLabel('Use ↑ ↓ to flip through'); tip.setProperty('kind', 'muted'); sv.addWidget(tip)
        sv.addWidget(self.pages, 3)
        hdr = QLabel('<b>In this plan</b>'); hdr.setStyleSheet('font-size:15px;margin-top:8px'); sv.addWidget(hdr)
        tip = QLabel('Double-click an area to redo it'); tip.setProperty('kind', 'muted'); sv.addWidget(tip)
        self.items = QListWidget(); self.items.setObjectName('planitems'); self.items.itemDoubleClicked.connect(lambda it: self.open_item(self.items.row(it)))
        sv.addWidget(self.items, 2)
        row = QHBoxLayout(); row.setSpacing(4)
        for txt, fn, tipx in [('↑', lambda: self.move_item(-1), 'Move up (earlier in the drawing)'), ('↓', lambda: self.move_item(1), 'Move down'),
                              ('Remove', self.remove_item, 'Take this area out of the plan')]:
            b = QPushButton(txt); b.setToolTip(tipx); b.clicked.connect(fn); row.addWidget(b)
        sv.addLayout(row)
        row = QHBoxLayout(); row.setSpacing(4)
        b = QPushButton('Add files…'); b.setToolTip('Add more of the builder\'s PDFs or DWGs to this plan'); b.clicked.connect(self.add_files); row.addWidget(b)
        sv.addLayout(row)
        self.saved_lbl = QLabel(''); self.tpl_lbl = QLabel('')       # not shown: the panel stays uncluttered
        d = QDockWidget('', self); d.setTitleBarWidget(QWidget()); d.setWidget(side); d.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, d); self.sheet_dock = d
        self._thumb_queue = []; self._thumb_timer = QTimer(self); self._thumb_timer.timeout.connect(self._next_thumb)
        self.tabs = QTabWidget(); self.tabs.setMinimumWidth(430); self.tabs.tabBar().hide()
        self.tabs.addTab(self._tab_area(), 'Plan area')
        self.tabs.addTab(self._tab_setup(), 'Builder setup')
        self.tabs.addTab(self._tab_review(), 'Review')
        d = QDockWidget('', self); d.setTitleBarWidget(QWidget()); d.setWidget(self.tabs); d.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.RightDockWidgetArea, d)

    def _step(self, n, title, text):
        l = QLabel(f'<div style="font-size:12px;color:#4a90b8;font-weight:700;letter-spacing:1px">STEP {n} OF 2</div>' if n else '<div></div>'
                   f'<div style="font-size:18px;font-weight:700;margin:2px 0 6px 0">{title}</div>'
                   f'<div style="color:#444">{text}</div>')
        l.setWordWrap(True); return l

    def _tab_area(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._step(1, 'Draw a box around the plan',
                               'Drag a box around the floor plan you want to clean. Leave out the legend, notes, '
                               'title block and option plans. Not right? Just drag a new box.<br><br>'
                               'Pinch or use the mouse wheel to zoom. Scroll with two fingers, hold the space bar, or use the middle mouse button to move around.'))
        self.area_lbl = QLabel(''); self.area_lbl.setWordWrap(True); v.addWidget(self.area_lbl)
        row = QHBoxLayout(); row.addWidget(QLabel('This is'))
        self.mode_cb = QComboBox(); self.mode_cb.addItem('found automatically', 'auto'); self.mode_cb.addItem('a plan or option', 'plan')
        self.mode_cb.addItem('an elevation', 'elevation')
        self.mode_cb.setToolTip('An elevation is recognised by its title (FRONT ELEVATION...). Pick it yourself if the title isn\'t in your box.')
        row.addWidget(self.mode_cb, 1); v.addLayout(row)
        self.clean_btn = _big('Clean up this area', primary=True); self.clean_btn.setEnabled(False)
        self.clean_btn.clicked.connect(self.clean_clicked); v.addWidget(self.clean_btn)
        self.prog = QWidget(); pv = QVBoxLayout(self.prog); pv.setContentsMargins(0, 8, 0, 0)
        self.prog_lbl = QLabel(''); pv.addWidget(self.prog_lbl)
        self.prog_bar = QProgressBar(); self.prog_bar.setRange(0, 0); self.prog_bar.setTextVisible(False); self.prog_bar.setFixedHeight(8); pv.addWidget(self.prog_bar)
        self.prog_cancel = QPushButton('Cancel'); pv.addWidget(self.prog_cancel, 0, Qt.AlignLeft)
        self.prog.hide(); v.addWidget(self.prog)
        v.addStretch(); return w

    def clean_clicked(self):
        if self.profile is not None and not (self.profile.symbols or self.profile.dwg_block_map):
            self.open_teach(); return           # new builder: teach symbols first
        self.run_clean()

    def _tab_setup(self):
        w = QWidget(); v = QVBoxLayout(w)
        row = QHBoxLayout(); back = QPushButton('← Back'); back.setToolTip('Leave teaching and go back to where you were')
        back.clicked.connect(self.close_teach); row.addWidget(back); row.addStretch(); v.addLayout(row)
        v.addWidget(self._step(0, 'Teach this builder\'s symbols', 'Show the app what this builder\'s symbols look like: '
                               'click <b>Click a symbol to teach it</b>, click one of each kind of symbol on the plan and say what it is. '
                               'You only do this once per builder. Click <b>← Back</b> any time to leave.'))
        v.addWidget(self._help('Do this once per builder. Tell the tool what each line type is, then click one example of each '
                               'symbol and choose what it is. Save when done; later plans from this builder skip this step.'))
        row = QHBoxLayout(); row.addWidget(QLabel('Drawing scale')); self.scale_cb = QComboBox(); self.scale_cb.addItems([s[0] for s in SCALES])
        self.scale_cb.currentIndexChanged.connect(self.on_scale); row.addWidget(self.scale_cb); v.addLayout(row)
        self.roles_lbl = QLabel('Line types on this plan'); v.addWidget(self.roles_lbl)
        self.roles = QTableWidget(0, 3); self.roles.setHorizontalHeaderLabels(['Line type', 'Count', 'What it is'])
        self.roles.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.roles.verticalHeader().hide()
        self.roles.itemSelectionChanged.connect(self.on_role_select); self.roles.cellClicked.connect(self._role_clicked)
        self.roles.setSelectionBehavior(QTableWidget.SelectRows); self.roles.setSelectionMode(QTableWidget.SingleSelection)
        v.addWidget(self.roles, 2)
        b = QPushButton('Clear highlight'); b.setToolTip('Stop highlighting the line type (or press Esc, or click the row again)')
        b.clicked.connect(lambda: self.roles.clearSelection()); v.addWidget(b, 0, Qt.AlignLeft)
        self.sym_lbl = QLabel('Symbol examples'); v.addWidget(self.sym_lbl)
        self.examples = QTableWidget(0, 3); self.examples.setHorizontalHeaderLabels(['Builder symbol', 'Is a…', ''])
        self.examples.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch); self.examples.verticalHeader().hide()
        self.examples.setSelectionBehavior(QTableWidget.SelectRows)
        self.examples.setIconSize(QPixmap(56, 56).size()); v.addWidget(self.examples, 2)
        a = QAction(self.examples); a.setShortcuts([QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)])
        a.setShortcutContext(Qt.WidgetShortcut); a.triggered.connect(self.remove_example); self.examples.addAction(a)
        hint = QLabel('Red cross = the centre it learned. Off? Click <b>Re-centre</b> on that row, then click the symbol\'s centre again. '
                      '<b>×</b> deletes it.'); hint.setWordWrap(True); hint.setProperty('kind', 'muted'); v.addWidget(hint)
        row = QHBoxLayout()
        self.teach_btn = QPushButton('Click a symbol to teach it'); self.teach_btn.clicked.connect(self.start_teach); row.addWidget(self.teach_btn)
        b = QPushButton('Delete selected'); b.clicked.connect(lambda: self.remove_example()); row.addWidget(b); v.addLayout(row)
        b = QPushButton('Builder settings (what to show)…'); b.clicked.connect(self.builder_settings); v.addWidget(b)
        b = _big('Done: save and clean the area', primary=True); b.clicked.connect(self.save_and_clean); v.addWidget(b)
        return w

    def open_teach(self):
        if not self.profile: QMessageBox.information(self, 'Teach symbols', 'Start or open a plan first.'); return
        self._teach_from = self.tabs.currentIndex() if self.tabs.currentIndex() != 1 else 0
        self.tabs.setCurrentIndex(1)

    def close_teach(self):
        self.roles.clearSelection()
        if self.view.mode == 'teach': self.view.set_mode('review' if self.result else 'box')
        self.tabs.setCurrentIndex(2 if self.result else getattr(self, '_teach_from', 0) or 0)

    def builder_settings(self):
        if not self.profile: QMessageBox.information(self, 'Builder', 'Choose or create a builder first.'); return
        if BuilderSettingsDialog(self.profile, self).exec() == QDialog.Accepted:
            self.save_profile()
            if self.sheet: self.run_clean()

    def save_and_clean(self):
        self.save_profile(); self.run_clean()

    def _tab_review(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._step(2, 'Check it and add it to the plan', 'Anything the app wasn\'t sure about is in <span style="color:#d00000">red</span> and listed below. '
                               'Click an item to jump to it, then change its type, delete it, or mark it as right.'))
        self.summary = QLabel(''); self.summary.setWordWrap(True); v.addWidget(self.summary)
        self.show_removed = QCheckBox('Show what was removed (faded red)'); self.show_removed.toggled.connect(self.draw_result); v.addWidget(self.show_removed)
        self.show_orig = QCheckBox('Show the builder\'s whole sheet underneath'); self.show_orig.setChecked(False); self.show_orig.toggled.connect(lambda on: self.orig_btn.setChecked(on) if self.orig_btn.isChecked() != on else self.draw_result()); self.show_orig.hide()   # state only: the Show original (O) button is the control
        v.addWidget(QLabel('<b>Needs a look</b>'))
        self.review = QListWidget(); self.review.itemClicked.connect(self.on_review_item); v.addWidget(self.review, 3)
        row = QHBoxLayout(); self.edit_type = QComboBox(); self.edit_type.addItems([v_[0] for v_ in SYMBOL_TYPES.values()])
        self.edit_type.activated.connect(self.change_type); row.addWidget(self.edit_type, 1)
        b = QPushButton('Delete'); b.clicked.connect(self.delete_sel); row.addWidget(b)
        b = QPushButton('Looks right'); b.clicked.connect(self.ok_sel); row.addWidget(b); v.addLayout(row)
        row = QHBoxLayout(); row.addWidget(QLabel('This area is'))
        self.area_type = QComboBox(); self.area_type.addItems(AREA_TYPES); row.addWidget(self.area_type, 1)
        self.area_type.activated.connect(self.area_type_changed)
        self.for_lbl = QLabel('for'); row.addWidget(self.for_lbl); self.elev_cb = QComboBox(); self.elev_cb.setToolTip('Which elevation\'s box it goes in on the master template. '
                     'Include the elevation name in your box and it\'s picked for you.'); row.addWidget(self.elev_cb, 1); v.addLayout(row)
        row = QHBoxLayout(); self.level_lbl = QLabel('Level'); row.addWidget(self.level_lbl)
        self.level_cb = QComboBox(); [self.level_cb.addItem(n, n) for n in LEVELS]
        self.level_cb.setToolTip('Floors of one plan go side by side on the template, left to right: upper, main, lower. '
                                 'It\'s read from the sheet title (2ND FLOOR, BASEMENT...); change it if it\'s wrong.')
        row.addWidget(self.level_cb, 1); row.addStretch(2); v.addLayout(row)
        self.area_type.currentTextChanged.connect(lambda t: (self.level_lbl.setVisible(t == 'Electrical plan'), self.level_cb.setVisible(t == 'Electrical plan')))
        self.add_btn = _big('Add to plan', primary=True); self.add_btn.clicked.connect(self.add_to_plan); v.addWidget(self.add_btn)
        self.add_hint = QLabel('Then box the next area. When you\'re done, click <b>Export DWG</b> at the top right.'); self.add_hint.setProperty('kind', 'muted')
        self.add_hint.setWordWrap(True); v.addWidget(self.add_hint)
        row = QHBoxLayout()
        b = QPushButton('Redraw the box'); b.clicked.connect(self.redraw_box); row.addWidget(b)
        b = QPushButton('Teach symbols…'); b.clicked.connect(self.open_teach); row.addWidget(b)
        b = QPushButton('Start over'); b.setToolTip('Run the cleanup again on the same box. Your changes are lost.')
        b.clicked.connect(self.start_over); row.addWidget(b); v.addLayout(row)
        return w

    def start_over(self):
        if QMessageBox.question(self, 'Start over', 'Run the cleanup again on the same box?\n\nAny changes you made in the review will be lost.') == QMessageBox.Yes:
            self.run_clean()

    def redraw_box(self):
        self.result = None; self._hide_orig_btn(); self.view.clear_overlay(); self.tabs.setCurrentIndex(0); self.view.set_mode('box')
        self.clean_btn.setEnabled(self.region is not None)

    def _help(self, text):
        l = QLabel(text); l.setWordWrap(True); l.setStyleSheet('color:#4b5a66;background:#f1f6f9;padding:10px;border-radius:6px'); return l

    # ---------- builders
    def set_builder(self, name):
        self.profile = Profile.load(self.settings.profile_path(name)) if name else None
        self.after_profile_change(); self.update_info()

    def after_profile_change(self):
        if self.profile:
            k = round(self.profile.inches_per_pt * 72)
            for i, (_, v) in enumerate(SCALES):
                if v == k: self.scale_cb.setCurrentIndex(i)
        self.fill_setup()

    def save_profile(self):
        if not self.profile: QMessageBox.information(self, 'Builder', 'Choose or create a builder first.'); return
        self.profile.save(self.settings.profile_path(self.profile.name))
        self.statusBar().showMessage(f'Saved builder setup "{self.profile.name}".', 5000)

    def on_scale(self, i):
        if self.profile: self.profile.inches_per_pt = SCALES[i][1] / 72

    # ---------- files
    def new_cleanup(self, files=()):
        d = StartDialog(self.settings, self, open_settings=lambda parent: self.open_settings(parent))
        for f in files: d.set_file(f)
        if self.profile and d.builder.findText(self.profile.name) > 0: d.builder.setCurrentIndex(d.builder.findText(self.profile.name))
        if d.exec() != QDialog.Accepted: return
        if d.open_folder: self.open_plan(d.open_folder); return
        self.plan = Plan.create(d.plan_name.text().strip(), d.builder_name(), d.paths)
        self.plan.project = d.project.text().strip(); self.plan.elevations = [e.strip() for e in d.elevations.text().split(',') if e.strip()]
        self.plan.save()
        self.set_builder(d.builder_name())
        self.load(d.paths, d.sheet_index); self.fill_items()

    def open_plan_dialog(self):
        d = OpenPlanDialog(self)
        if d.exec() == QDialog.Accepted and d.folder: self.open_plan(d.folder)

    def open_plan(self, folder):
        try: plan = Plan.load(folder)
        except Exception as e: QMessageBox.warning(self, 'Open plan', f'Could not open that plan:\n{e}'); return
        missing = [f for f in plan.files if not Path(f).exists()]
        if missing:
            QMessageBox.warning(self, 'Files moved', 'These files aren\'t where they were, so their sheets aren\'t shown. '
                                'The areas you already cleaned are still in the plan.\n\n' + '\n'.join(missing) +
                                '\n\nUse Add files… to add them again from their new place.')
        self.plan = plan
        if plan.builder and plan.builder in self.settings.list_profiles(): self.set_builder(plan.builder)
        self.load([f for f in plan.files if Path(f).exists()]); self.fill_items()

    def add_files(self):
        if not self.plan: self.new_cleanup(); return
        fs, _ = QFileDialog.getOpenFileNames(self, 'Add the builder\'s files', '', 'Plans (*.pdf *.dwg)')
        self._add_files(fs)

    def _add_files(self, fs):
        fs = [f for f in fs if f not in self.plan.files]
        if not fs: return
        self.plan.add_files(fs); keep = self.pages.currentRow()
        self.load([f for f in self.plan.files if Path(f).exists()], max(keep, 0))

    def edit_names(self):
        if not self.plan: return
        dlg = QDialog(self); dlg.setWindowTitle('Plan names'); f = QFormLayout(dlg)
        n = QLineEdit(self.plan.name); p = QLineEdit(self.plan.project); e = QLineEdit(', '.join(self.plan.elevations))
        e.setPlaceholderText('e.g. Modern, Traditional, Farmhouse')
        p.setPlaceholderText('Fills PROJECT NAME on the template'); f.addRow('Plan name', n); f.addRow('Community', p); f.addRow('Elevation names', e)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); f.addRow(bb)
        dlg.resize(460, 160)
        if dlg.exec() != QDialog.Accepted: return
        self.plan.name = n.text().strip() or self.plan.name; self.plan.project = p.text().strip()
        self.plan.elevations = [x.strip() for x in e.text().split(',') if x.strip()]; self.plan.save(); self.fill_items()

    # ---------- the plan's areas
    def fill_items(self):
        self.items.clear()
        if not self.plan: self.save_btn.setEnabled(self.result is not None); return
        for i, it in enumerate(self.plan.items):
            typ = f' ({it.area_type})' if it.area_type != 'Electrical plan' else f' ({it.elevation})' if it.elevation else ''
            w = QListWidgetItem(f'{i + 1}. {it.label}{typ}'); w.setSizeHint(QSize(160, 30)); self.items.addItem(w)
        self.saved_lbl.setText('✓ Saved automatically; reopen it with File ▸ Open plan.<br><b>Done adding areas?</b> Click <b>Export DWG</b> at the top right.'
                               if self.plan.items else '')
        self.saved_lbl.setTextFormat(Qt.RichText)
        self.new_act_tb.setVisible(False); self.open_act_tb.setVisible(False); self.names_act_tb.setVisible(True)
        self.show_done_boxes()
        t = self.settings.master_template
        self.tpl_lbl.setText(f'Template: {Path(t).name} (change it in Settings)' if t else 'Template: none, set one in Settings')
        self.save_btn.setEnabled(True)
        self.setWindowTitle(f'Plan Cleanup {__version__} — {self.plan.name}'); self.update_info()

    def _sheet_index(self, file, page):
        for i, sh in enumerate(self.sheets):
            if getattr(sh, 'path', None) == file and (sh.kind != 'pdf' or sh.index == page): return i
        return -1

    def add_to_plan(self):
        if not (self.result and self.sheet): return
        if not self.plan:
            self.plan = Plan.create(Path(self.file_name).stem or 'Plan', self.profile.name if self.profile else '',
                                    sorted({sh.path for sh in self.sheets}))
        if self.editing is not None and self.editing < len(self.plan.items):
            self.plan.replace(self.editing, self.result, self.region); n = self.editing
            self.plan.items[n].area_type = self.area_type.currentText(); self.plan.items[n].elevation = self.elev_cb.currentData() or ''
            self.plan.items[n].level = self.level_cb.currentData() or ''; self.plan.save()
            msg = f'Updated area {n + 1} in the plan.'
        else:
            lab = getattr(self.sheet, 'label', self.sheet.title)
            self.plan.add(self.result, self.sheet.path, getattr(self.sheet, 'index', 0), self.sheet.kind, self.region, lab, self.area_type.currentText(),
                          self.elev_cb.currentData() or '', self.level_cb.currentData() if not is_elev(self.area_type.currentText()) else '')
            msg = f'Added to the plan ({len(self.plan.items)} area{"s" if len(self.plan.items) != 1 else ""}). Box the next area, or Export DWG when you\'re done.'
        self.editing = None; self.fill_items(); self.redraw_box(); self.statusBar().showMessage(msg, 8000)
        QTimer.singleShot(0, self.view.fit)            # back out to the whole page for the next box

    def open_item(self, i):
        it = self.plan.items[i]; si = self._sheet_index(it.file, it.page)
        if it.kind == 'pdf' and not self.profile: QMessageBox.information(self, 'Redo area', 'Choose the builder first (New plan).'); return
        if si < 0:
            QMessageBox.information(self, 'Redo area', f'The file this area came from isn\'t in the plan any more:\n{it.file}\n\nUse Add files… to add it again.'); return
        self.pages.setCurrentRow(si); self.on_page(si)
        self.region = tuple(it.region); self.editing = i
        self.show_done_boxes()
        (ax, ay), (bx, by) = self.sheet.to_view(it.region[0], it.region[1]), self.sheet.to_view(it.region[2], it.region[3])
        self.view.show_region(QRectF(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)))
        res = reattach(self.plan.result(it), it.kind, self.profile, it.region)
        self.on_cleaned(res)
        self.statusBar().showMessage(f'Redoing area {i + 1}. Fix it up, then click Update in plan. Redraw the box to clean it again.')

    def remove_item(self):
        i = self.items.currentRow()
        if not self.plan or i < 0: return
        if QMessageBox.question(self, 'Remove area', f'Take "{self.plan.items[i].label}" out of the plan?') != QMessageBox.Yes: return
        self.plan.remove(i)
        if self.editing is not None: self.editing = None if self.editing == i else self.editing - (self.editing > i)
        self.fill_items()

    def move_item(self, d):
        i = self.items.currentRow()
        if not self.plan or i < 0 or not 0 <= i + d < len(self.plan.items): return
        self.plan.move(i, i + d)
        if self.editing in (i, i + d): self.editing = i + d if self.editing == i else i
        self.fill_items(); self.items.setCurrentRow(i + d)

    def _next_thumb(self):
        if not self._thumb_queue: self._thumb_timer.stop(); return
        i = self._thumb_queue.pop(0)
        if i >= len(self.sheets) or self.sheets[i].kind != 'pdf': return
        page = self.sheets[i].page; z = THUMB_W / max(page.rect.width, page.rect.height)
        pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888).copy()
        it = self.pages.item(i)
        if it: it.setIcon(QIcon(QPixmap.fromImage(img)))

    def load(self, files, index=0):
        """Show every sheet of the plan's files in the sheet list (several files: grouped by file)."""
        if isinstance(files, (str, Path)): files = [str(files)]
        self.result = None; self.region = None; self.sheets = []; self.sheet = None; self.editing = None
        self.pages.blockSignals(True); self.pages.clear(); self._thumb_queue = []
        many = len(files) > 1
        for f in files:
            ext = Path(f).suffix.lower()
            try:
                with open(f, 'rb') as fh: is_pdf = fh.read(5) == b'%PDF-'
            except OSError: is_pdf = ext == '.pdf'
            if is_pdf:
                try: doc = pymupdf.open(f)
                except Exception as e: QMessageBox.warning(self, 'Can\'t open PDF', f'{Path(f).name}\n{e}'); continue
                for i in range(len(doc)):
                    sh = PdfSheet(doc, i); sh.path = f
                    sh.label = f'{Path(f).stem} · Page {i + 1}' if many and len(doc) > 1 else (Path(f).stem if many else f'Page {i + 1}')
                    self.sheets.append(sh)
            else:
                exe = oda.find_converter(self.settings.oda_path)
                try: dxf = oda.to_dxf(f, exe)
                except Exception as e: QMessageBox.warning(self, 'Can\'t open DWG', f'{Path(f).name}\n{e}'); continue
                self.dwg_doc = dwgin.open_drawing(dxf)
                sh = DwgSheet(self.dwg_doc, Path(f).name); sh.path = f; sh.label = Path(f).stem; self.sheets.append(sh)
        self.file_name = Path(files[0]).name if files else ''
        self.setWindowTitle(f'Plan Cleanup {__version__} — {self.plan.name if self.plan else self.file_name}')
        for sh in self.sheets:
            it = QListWidgetItem(sh.label); it.setSizeHint(QSize(160, 34)); self.pages.addItem(it)
        self.pages.blockSignals(False)
        if self.sheets: self.pages.setCurrentRow(min(index, len(self.sheets) - 1)); self.pages.setFocus()
        else: self.view.clear_overlay(); self.update_info()

    def on_page(self, i):
        if i < 0 or i >= len(self.sheets): return
        self.sheet = self.sheets[i]; self.region = None; self.result = None; self.teacher = None; self.editing = None
        self._hide_orig_btn(); self.view.set_sheet(self.sheet.pixmap()); self.view.set_mode('box')
        self.area_lbl.setText(''); self.clean_btn.setEnabled(False); self.tabs.setCurrentIndex(0); self.fill_setup(); self.update_info()
        self.show_done_boxes()
        self.statusBar().showMessage('Find the electrical sheet (↑ ↓), then drag a box around the floor plan.')

    def show_done_boxes(self):
        """Areas already in the plan stay outlined on their sheet (green), so nothing is boxed twice."""
        if not (self.plan and self.sheet): return
        boxes = []
        for n, it in enumerate(self.plan.items):
            if n == self.editing or it.file != self.sheet.path or (it.kind == 'pdf' and it.page != getattr(self.sheet, 'index', 0)): continue
            (ax, ay), (bx, by) = self.sheet.to_view(it.region[0], it.region[1]), self.sheet.to_view(it.region[2], it.region[3])
            boxes.append((QRectF(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)), f'{n + 1}. {it.area_type}'))
        self.view.show_done(boxes)

    def on_region(self, rect: QRectF):
        self.region = self.sheet.region_from_view(rect); self.view.set_mode('pan'); self.teacher = None

        if self.profile and self.sheet.kind == 'pdf' and not self.profile.pen_roles:
            paths = [p for p in self.sheet.page.get_drawings() if in_region(p, self.region)]
            self.profile.pen_roles = guess_roles(paths)
        self.fill_setup()
        if self.profile is None:
            QMessageBox.information(self, 'Builder', 'Choose a builder with New cleanup first.'); return
        self.view.set_mode('box')              # dragging again replaces the box
        self.clean_btn.setEnabled(True)
        self.area_lbl.setText('Box drawn. Click <b>Clean up this plan</b>, or drag a new box if it\'s not right.')
        self.statusBar().showMessage('Box drawn. Click Clean up this plan.')

    # ---------- setup tab
    def fill_setup(self):
        self.roles.setRowCount(0); self.examples.setRowCount(0)
        if not self.profile or not self.sheet: return
        if self.sheet.kind == 'pdf':
            self.roles_lbl.setText('Line types on this plan (select one to see it highlighted)')
            if not self.region: return
            paths = [p for p in self.sheet.page.get_drawings() if in_region(p, self.region)]
            st = sorted(pens_in(paths).items(), key=lambda kv: -kv[1]['count'])
            for k, s in st:
                self.profile.pen_roles.setdefault(k, 'misc')
                self._role_row(k, s['count'], self.profile.pen_roles, PEN_ROLES)
            self.sym_lbl.setText('Symbol examples (click one of each kind on the plan)')
            for ex in self.profile.symbols: self._example_row(ex)
            self.teach_btn.setEnabled(True)
        else:
            layers, blocks = dwgin.inventory(self.sheet.doc)
            self.roles_lbl.setText('Layers in this drawing')
            for k, n in layers.most_common(): self._role_row(k, n, self.profile.dwg_layer_roles, PEN_ROLES, default='misc')
            self.sym_lbl.setText('Builder blocks: choose what each one is (leave blank to keep as lines)')
            for k, n in blocks.most_common(): self._block_row(k, n)
            self.teach_btn.setEnabled(False)

    def _role_row(self, key, count, store, roles, default='misc'):
        r = self.roles.rowCount(); self.roles.insertRow(r)
        it = QTableWidgetItem(key); it.setData(Qt.UserRole, key); self.roles.setItem(r, 0, it)
        self.roles.setItem(r, 1, QTableWidgetItem(str(count)))
        cb = QComboBox(); cb.addItems(list(roles.values()))
        cb.setCurrentIndex(list(roles).index(store.get(key, default)))
        cb.currentIndexChanged.connect(lambda i, k=key: store.__setitem__(k, list(roles)[i]))
        self.roles.setCellWidget(r, 2, cb)

    def _block_row(self, name, count):
        r = self.examples.rowCount(); self.examples.insertRow(r)
        self.examples.setItem(r, 0, QTableWidgetItem(f'{name}  ({count})'))
        cb = QComboBox(); keys = [''] + [k for k in SYMBOL_TYPES]; cb.addItems(['(keep as lines)'] + [SYMBOL_TYPES[k][0] for k in keys[1:]])
        cur = self.profile.dwg_block_map.get(name, ''); cb.setCurrentIndex(keys.index(cur) if cur in keys else 0)
        def set_(i, n=name):
            if keys[i]: self.profile.dwg_block_map[n] = keys[i]
            else: self.profile.dwg_block_map.pop(n, None)
        cb.currentIndexChanged.connect(set_); self.examples.setCellWidget(r, 1, cb)

    def _example_row(self, ex):
        r = self.examples.rowCount(); self.examples.insertRow(r)
        it = QTableWidgetItem(); it.setIcon(self._example_icon(ex)); it.setData(Qt.UserRole, ex.id); self.examples.setItem(r, 0, it)
        self.examples.setRowHeight(r, 60)
        btns = QWidget(); h = QHBoxLayout(btns); h.setContentsMargins(2, 2, 2, 2); h.setSpacing(4)
        rc = QPushButton('Re-centre'); rc.setToolTip('Click the symbol\'s centre on the plan again to fix this example')
        rc.clicked.connect(lambda _=False, e=ex: self.recentre_example(e)); h.addWidget(rc)
        x = QPushButton('×'); x.setFixedWidth(30); x.setToolTip('Delete this example'); x.clicked.connect(lambda _=False, e=ex: self.remove_example(e)); h.addWidget(x)
        self.examples.setCellWidget(r, 2, btns); self.examples.resizeColumnToContents(2)
        cb = QComboBox(); keys = list(SYMBOL_TYPES); cb.addItems([SYMBOL_TYPES[k][0] for k in keys]); cb.setCurrentIndex(keys.index(ex.cls) if ex.cls in keys else 0)
        cb.currentIndexChanged.connect(lambda i, e=ex: (setattr(e, 'cls', keys[i]), self._save_quietly())); self.examples.setCellWidget(r, 1, cb)

    def on_role_select(self):
        if not self.sheet or self.sheet.kind != 'pdf' or not self.region: return
        rows = {i.row() for i in self.roles.selectedItems()}
        if not rows: self._clear_role_highlight(); return
        key = self.roles.item(min(rows), 0).data(Qt.UserRole)
        self.view.clear_overlay()
        polys = []
        from planclean.vec import polylines
        for p in self.sheet.page.get_drawings():
            if pen_key(p) == key and in_region(p, self.region):
                for q in polylines(p): polys.append([self.sheet.to_view(*v) for v in q])
        self.view.add_paths(polys, '#ff2d55', 2); self.view.page_item.setOpacity(0.35)

    def _clear_role_highlight(self):
        self.view.clear_overlay()
        if self.result and self.tabs.currentIndex() != 1: self.draw_result()
        elif self.view.page_item: self.view.page_item.setOpacity(1.0)

    def _role_clicked(self, row, _col):
        # clicking the highlighted line type again turns the highlight off
        if getattr(self, '_role_last', None) == row and self.roles.selectionModel().isRowSelected(row):
            self.roles.clearSelection(); self._role_last = None
        else: self._role_last = row

    def start_teach(self):
        if not self.profile: QMessageBox.information(self, 'Builder', 'Choose or create a builder first.'); return
        if not self.region: QMessageBox.information(self, 'Plan area', 'Draw a box around the plan first.'); return
        self.view.clear_overlay(); self.view.set_mode('teach')
        self.statusBar().showMessage('Click the centre of a builder symbol. Press Esc when done.')

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.roles.selectedItems(): self.roles.clearSelection(); return
            self._recentre = None
            self.view.set_mode('review' if self.result else 'pan'); self.statusBar().clearMessage()
        else: super().keyPressEvent(e)

    def on_teach_click(self, p: QPointF):
        if self.teacher is None:
            self.teacher = Teacher(self.sheet.page, self.region, self.profile)
        x, y = self.sheet.from_view(p.x(), p.y())
        old = getattr(self, '_recentre', None)
        if old is not None:                      # fixing an off-centre example: same type, new picture
            self._recentre = None
            ex, (cx, cy) = self.teacher.example_at(x, y, old.cls)
            ex.learned = getattr(old, 'learned', False)
            i = next((k for k, e in enumerate(self.profile.symbols) if e.id == old.id), None)
            if i is None: self.profile.symbols.append(ex)
            else: self.profile.symbols[i] = ex
            r = self._example_rowof(old.id)
            if r >= 0: self.examples.removeRow(r)
            self._example_row(ex); self._save_quietly()
            vx, vy = self.sheet.to_view(cx, cy); self.view.add_marker(vx, vy, CLASS_COLORS.get(ex.cls, '#000'), 'fixed')
            self.view.set_mode('review' if self.result else 'pan')
            self.statusBar().showMessage('Example re-centred and saved.', 5000); return
        labels = [v[0] for v in SYMBOL_TYPES.values()]
        choice, ok = QInputDialog.getItem(self, 'What is this symbol?', 'This builder symbol is a:', labels, 0, False)
        if not ok: return
        cls = list(SYMBOL_TYPES)[labels.index(choice)]
        # the pens under a taught symbol are electrical
        for k in self.teacher.pens_near(x, y, 4):
            if self.profile.pen_roles.get(k) in (None, 'misc') and not k.startswith('fill'): self.profile.pen_roles[k] = 'electrical'
        self.teacher.refresh()
        ex, (cx, cy) = self.teacher.example_at(x, y, cls)
        self.profile.symbols.append(ex); self._example_row(ex); self._save_quietly()
        vx, vy = self.sheet.to_view(cx, cy); self.view.add_marker(vx, vy, CLASS_COLORS.get(cls, '#000'), sym_label(cls).split(' (')[0])

    def _example_icon(self, ex):
        """The taught picture, bigger, with a red cross on the centre it learned."""
        img = 255 - ex.image().clip(0, 255).astype('uint8')
        q = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_Grayscale8).copy()
        pm = QPixmap.fromImage(q).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(pm); p.setPen(QPen(QColor(220, 40, 60), 1)); c = pm.width() // 2; m = pm.height() // 2
        p.drawLine(c - 6, m, c + 6, m); p.drawLine(c, m - 6, c, m + 6); p.end()
        return QIcon(pm)

    def _example_rowof(self, eid):
        for r in range(self.examples.rowCount()):
            if self.examples.item(r, 0) and self.examples.item(r, 0).data(Qt.UserRole) == eid: return r
        return -1

    def remove_example(self, ex=None):
        if not self.profile: return
        ids = [ex.id] if ex is not None and hasattr(ex, 'id') else [self.examples.item(r, 0).data(Qt.UserRole)
                                                                    for r in {i.row() for i in self.examples.selectedIndexes()}]
        if not ids: return
        for eid in ids:
            self.profile.symbols = [e for e in self.profile.symbols if e.id != eid]
            r = self._example_rowof(eid)
            if r >= 0: self.examples.removeRow(r)
        self._save_quietly(); self.statusBar().showMessage('Example deleted.', 4000)

    def recentre_example(self, ex):
        if not self.region: QMessageBox.information(self, 'Re-centre', 'Draw a box around the plan first.'); return
        self._recentre = ex; self.view.set_mode('teach')
        self.statusBar().showMessage(f'Click the centre of a {sym_label(ex.cls).split(" (")[0].lower()} on the plan. Esc to cancel.')

    # ---------- clean + review
    def run_clean(self, mode=None):
        if not (self.sheet and self.region and self.profile):
            missing = ('a plan (click New cleanup)' if not self.sheet else 'a builder (click New cleanup)' if not self.profile
                       else 'a box around the plan (drag one on the sheet)')
            QMessageBox.information(self, 'Not ready', f'Still needed: {missing}.'); return
        prof = self.profile
        if self.sheet.kind == 'pdf':
            mode = mode or self.mode_cb.currentData()
            if not prof.symbols and mode != 'elevation': QMessageBox.information(self, 'Builder setup', 'Teach this builder at least one symbol first (Teach symbols…).'); return
            self.run_pdf_job(self.sheet.doc.name, self.sheet.index, self.region, prof, mode); return
        else:
            doc, region = self.sheet.doc, self.region
            fn = lambda prog: dwgin.clean_dwg(doc, region, prof, progress=prog)
        run_in_thread(self, fn, self.on_cleaned, 'Cleaning the plan…')

    def run_pdf_job(self, path, index, region, prof, mode='auto'):
        job = CleanJob(path, index, region, prof, mode)
        self.tabs.setCurrentIndex(0); self.clean_btn.setEnabled(False); self.prog.show()
        state = {'step': 'Starting'}
        timer = QTimer(self)
        def stop():
            timer.stop(); self.prog.hide(); self.clean_btn.setEnabled(True); self._job = None
            try: self.prog_cancel.clicked.disconnect()
            except Exception: pass
        def tick():
            for kind, payload in job.poll():
                if kind == 'progress': state['step'] = payload
                elif kind == 'done': stop(); self.on_cleaned(attach_to_page(payload, prof, region)); return
                elif kind == 'failed': stop(); QMessageBox.critical(self, 'Something went wrong', str(payload)[-2000:]); return
            self.prog_lbl.setText(f"<b>Cleaning…</b> {state['step']} ({int(job.elapsed())} s)")
            self.statusBar().showMessage(f"Cleaning: {state['step']}")
        def cancel():
            if job.finished: return
            job.cancel(); stop(); self.statusBar().showMessage('Cleaning cancelled.')
        self.prog_cancel.clicked.connect(cancel)
        timer.timeout.connect(tick)
        self._job = job; job.start(); tick(); timer.start(200)

    def on_cleaned(self, res: Result):
        self.result = res; self.tabs.setCurrentIndex(2); self.view.set_mode('review'); self.statusBar().clearMessage()
        from collections import Counter
        c = Counter(s.cls for s in res.symbols)
        parts = ', '.join(f'{n} {sym_label(k).split(" (")[0].lower()}' for k, n in c.most_common())
        self.summary.setText(f'<b>{len(res.symbols)} symbols:</b> {parts}.<br><b>{sum(1 for t in res.texts if t.kind == "room")} room names</b>, '
                             f'{sum(1 for t in res.texts if t.kind == "note")} notes kept, {len(res.dropped_labels)} builder labels removed.')
        self.draw_result(); self.fill_review(); self.orig_btn.show(); self.orig_btn.raise_()
        self.save_btn.setEnabled(True)
        QTimer.singleShot(0, self.view.fit)            # zoom to the box you drew
        self.add_btn.setText('Update in plan' if self.editing is not None else 'Add to plan')
        elev = res.stats.get('kind') == 'elevation'
        if self.editing is not None and self.plan and is_elev(self.plan.items[self.editing].area_type) == elev: guess = self.plan.items[self.editing].area_type
        elif elev: guess = 'Interior elevation' if is_elev(self.area_type.currentText()) and 'Interior' in self.area_type.currentText() else 'Exterior elevation'
        else: guess = 'Option' if any(t.kind == 'title' and 'OPT' in ' '.join(t.lines) for t in res.texts) else 'Electrical plan'
        if self.area_type.findText(guess) >= 0: self.area_type.setCurrentText(guess)
        self.for_lbl.setVisible(not elev); self.elev_cb.setVisible(not elev)
        if elev:
            self.summary.setText(f'<b>Elevation.</b> {res.stats.get("removed", 0)} pieces of notes, dimensions, pitch marks and leaders taken off. '
                                 'The building and its title are kept. Turn on <i>Show what was removed</i> to check.')
        self.elev_cb.clear(); self.elev_cb.addItem('first free elevation', '')
        for n in (self.plan.elevations if self.plan else []): self.elev_cb.addItem(n, n)
        if self.editing is not None and self.plan: ev = self.plan.items[self.editing].elevation
        else: ev = self.guess_elevation(res)
        i = self.elev_cb.findData(ev); self.elev_cb.setCurrentIndex(max(i, 0))
        lv = self.plan.items[self.editing].level if (self.editing is not None and self.plan) else ''
        self.level_cb.setCurrentIndex(LEVELS.index(lv) if lv in LEVELS else level_rank(res))
        show = self.area_type.currentText() == 'Electrical plan'; self.level_lbl.setVisible(show); self.level_cb.setVisible(show)

    def area_type_changed(self, _=None):
        """Switching between a plan and an elevation cleans the box again the other way."""
        if not self.result: return
        want = is_elev(self.area_type.currentText())
        if want != (self.result.stats.get('kind') == 'elevation') and self.sheet and self.sheet.kind == 'pdf':
            self.run_clean('elevation' if want else 'plan')

    def guess_elevation(self, res):
        """An elevation name written inside the box (e.g. 'ELEVATION B - MODERN') picks the elevation."""
        if not self.plan: return ''
        words = ' ' + ' '.join(' '.join(t.lines) for t in res.texts).upper() + ' '
        import re, difflib
        for n in self.plan.elevations:
            if re.search(r'\b' + re.escape(n.upper()) + r'\b', words): return n
        toks = re.findall(r'[A-Z]{4,}', words)
        for n in self.plan.elevations:
            if any(difflib.SequenceMatcher(None, n.upper(), t).ratio() >= 0.8 for t in toks): return n
        # a name you haven't typed yet: 'ELEVATION A - MODERN' adds Modern to the plan's elevations
        m = None
        for ln in (l.upper().strip() for t in res.texts for l in t.lines):
            m = re.search(r'ELEV(?:ATION)?\.?\s+[A-Z0-9]?\s*[-–:]\s*([A-Z][A-Z ]{2,})$', ln)
            if m: break
        if m and len(self.plan.elevations) < 3:
            n = m.group(1).strip().title()
            if n.upper() not in ('PLAN', 'ELECTRICAL', 'ELECTRICAL PLAN'):
                self.plan.elevations.append(n); self.plan.save(); self.elev_cb.addItem(n, n); return n
        return ''

    def _hide_orig_btn(self):
        self.orig_btn.blockSignals(True); self.orig_btn.setChecked(False); self.orig_btn.setText('Show original  (O)')
        self.orig_btn.blockSignals(False); self.orig_btn.hide()
        self.show_orig.blockSignals(True); self.show_orig.setChecked(False); self.show_orig.blockSignals(False)
        if self.view.page_item is not None: self.view.page_item.setOpacity(1.0)

    def _orig_toggled(self, on):
        self.orig_btn.setText('Hide original  (O)' if on else 'Show original  (O)')
        if self.show_orig.isChecked() != on: self.show_orig.setChecked(on)   # redraws
        else: self.draw_result()

    def _view_pt(self, X, Y):
        return self.sheet.to_view(*self.result.to_page(X, Y))

    def draw_result(self):
        if not self.result: return
        self.view.clear_overlay()
        self.view.page_item.setOpacity(0.45 if self.show_orig.isChecked() else 0.0)
        bg = self.view.scene_.addRect(self.view.sceneRect(), Qt.NoPen, QColor('white')); bg.setZValue(-1); self.view.overlay.append(bg)
        polys = {}
        shapes = self.block_shapes()
        # wires end where your block's POINT says (e.g. the centre of a wall light's circle)
        wire_lines, wire_arcs = dxfout.retarget_wires(self.result, lambda s: shapes.block(self.settings.block_for(s.cls)))
        solids = []
        for role, pts, f in wire_lines:
            polys.setdefault(role, []).append([self._view_pt(*p) for p in pts])
            if f and len(pts) > 2: solids.append([self._view_pt(*p) for p in pts])
        if solids: self.view.add_paths(solids, '#111', 0, fill=True)
        from planclean.wiring import arc_points
        if self.show_removed.isChecked() and getattr(self.result, 'original', None):
            it = self.view.add_paths([[self._view_pt(*p) for p in pts] for pts in self.result.original], '#f08c8c', 0)
            it.setOpacity(0.7); it.setZValue(5)
        for role, cx, cy, r, a0, a1 in wire_arcs:
            polys.setdefault(role, []).append([self._view_pt(*p) for p in arc_points(cx, cy, r, a0, a1)])
        for role, ps in polys.items():
            self.view.add_paths(ps, {'wall': '#111', 'wiring': '#555'}.get(role, '#888'), 1.5 if role == 'wall' else 0)
        shapes = self.block_shapes()
        for s in self.result.symbols:
            if s.cls == 'remove': continue
            x, y = self._view_pt(s.x, s.y)
            polys = [[self._view_pt(*p) for p in pts] for pts in shapes.placed(s, self.settings.block_for(s.cls))]
            col = '#d00000' if s.review else '#000000'
            it = self.view.add_paths(polys, col, 0); it.setZValue(8); it.setData(0, s)
            for tx_, ty_, th, trot, ttxt in shapes.placed_texts(s, self.settings.block_for(s.cls)):
                # text insert is its baseline-left corner; draw it the same way
                # the sheet can be turned relative to the drawing, so map the text's own frame
                # (its baseline direction and its up direction) through the same transform as the lines
                ppi = self.view_px_per_inch()
                vx, vy = self._view_pt(tx_, ty_); f = QFont('Arial'); f.setPixelSize(max(2, int(round(th * ppi * 1.35))))
                ti = self.view.scene_.addSimpleText(ttxt, f); ti.setBrush(QColor(col))
                asc = ti.boundingRect().height() * 0.78
                c_, s_ = math.cos(math.radians(trot)), math.sin(math.radians(trot))
                ax, ay = self._view_pt(tx_ + c_, ty_ + s_); ux, uy = self._view_pt(tx_ - s_, ty_ + c_)
                X = ((ax - vx) / ppi, (ay - vy) / ppi); Y = ((ux - vx) / ppi, (uy - vy) / ppi)
                ti.setTransform(QTransform(X[0], X[1], -Y[0], -Y[1], vx + asc * Y[0], vy + asc * Y[1]))
                ti.setZValue(8); ti.setData(0, s); self.view.overlay.append(ti)
            # an invisible, clickable area over the symbol; review items get a red ring
            hit = self.view.add_marker(x, y, '#d00000' if s.review else '#00000000', payload=s, ring=bool(s.review), r=11)
            hit.setBrush(QColor(0, 0, 0, 1))
        px_per_in = self.view_px_per_inch()
        for t in self.result.texts:
            x, y = self._view_pt(t.x, t.y)
            h_in = {'room': self.settings.text_height_room, 'title': self.settings.text_height_title}.get(t.kind, self.settings.text_height_note)
            f = QFont('Arial'); f.setPixelSize(max(2, int(round(h_in * px_per_in / 0.72))))
            it = self.view.scene_.addSimpleText('\n'.join(t.lines), f)
            it.setBrush(QColor('#d00000' if t.review else '#000000'))
            br = it.boundingRect(); it.setPos(x - br.width() / 2, y - br.height() / 2)
            it.setZValue(9); it.setData(0, t); self.view.overlay.append(it)

    def view_px_per_inch(self):
        a = self._view_pt(0, 0); b = self._view_pt(12, 0)
        return math.hypot(b[0] - a[0], b[1] - a[1]) / 12

    def block_shapes(self):
        if getattr(self, '_shapes', None) is None or self._shapes.doc is not self.library():
            self._shapes = BlockShapes(self.library())
        return self._shapes

    def fill_review(self):
        self.review.clear()
        for s in self.result.symbols:
            if s.review:
                it = QListWidgetItem(f'{sym_label(s.cls).split(" (")[0]}: {s.review}'); it.setData(Qt.UserRole, s); self.review.addItem(it)
        for t in self.result.texts:
            if t.review:
                it = QListWidgetItem(f'Note "{" ".join(t.lines)}": {t.review}'); it.setData(Qt.UserRole, t); self.review.addItem(it)
        self._sel = None

    def on_review_item(self, it):
        self.on_pick(it.data(Qt.UserRole), zoom=True)

    def highlight(self, x, y, zoom=True):
        """Draw a box around the item being looked at and zoom to it."""
        old = getattr(self, '_hl', None)
        if old is not None and old.scene() is not None: self.view.scene_.removeItem(old)
        r = 22 * max(1.0, self.view_px_per_inch() * 8 / 22)
        from PySide6.QtGui import QPen, QBrush
        self._hl = self.view.scene_.addRect(QRectF(x - r, y - r, 2 * r, 2 * r), QPen(QColor('#4a90b8'), 0), QBrush(QColor(142, 197, 222, 90)))
        self._hl.setPen(QPen(QColor('#4a90b8'), 3)); self._hl.pen().setCosmetic(True)
        p = self._hl.pen(); p.setCosmetic(True); self._hl.setPen(p)
        self._hl.setZValue(20); self.view.overlay.append(self._hl)
        if zoom: self.view.fitInView(QRectF(x - 8 * r, y - 5 * r, 16 * r, 10 * r), Qt.KeepAspectRatio)

    def on_pick(self, obj, zoom=False, ask=True):
        self._sel = obj
        if hasattr(obj, 'cls'):
            self.edit_type.setCurrentIndex(list(SYMBOL_TYPES).index(obj.cls))
            x, y = self._view_pt(obj.x, obj.y); self.highlight(x, y, zoom)
            self.statusBar().showMessage(f'Selected: {sym_label(obj.cls)} (match {obj.score:.2f}) {" ".join(obj.mods)}')
        else:
            x, y = self._view_pt(obj.x, obj.y); self.highlight(x, y, zoom)
            self.statusBar().showMessage(f'Selected note: {" ".join(obj.lines)}. Click it again to edit or delete it, or use the buttons.')
            if ask: self.edit_note(obj)

    def edit_note(self, obj):
        """Change a note's words, or delete it."""
        d = QDialog(self); d.setWindowTitle('Note'); v = QVBoxLayout(d)
        v.addWidget(QLabel('Text (use / for a new line):'))
        ed = QLineEdit(' / '.join(obj.lines)); ed.setMinimumWidth(420); v.addWidget(ed)
        bb = QDialogButtonBox(); save = bb.addButton('Save', QDialogButtonBox.AcceptRole)
        dele = bb.addButton('Delete note', QDialogButtonBox.DestructiveRole); bb.addButton('Cancel', QDialogButtonBox.RejectRole)
        choice = {}
        save.clicked.connect(lambda: (choice.setdefault('a', 'save'), d.accept()))
        dele.clicked.connect(lambda: (choice.setdefault('a', 'delete'), d.accept()))
        bb.rejected.connect(d.reject); v.addWidget(bb)
        if not d.exec(): return
        self._snap()
        if choice.get('a') == 'delete' or not ed.text().strip(('/ ')):
            if obj in self.result.texts: self.result.texts.remove(obj)
            self._sel = None
        else:
            obj.lines = [l.strip() for l in ed.text().split('/') if l.strip()]; obj.review = ''
        self._after_review_change(); self.orig_btn.show(); self.orig_btn.raise_()

    def _snap(self):
        """Remember the review as it is now, so the next change can be undone."""
        if not self.result: return
        import copy as _copy
        prof = (list(self.profile.symbols), list(self.profile.drop_labels)) if self.profile else None
        self._undo.append((self.result, _copy.deepcopy((self.result.symbols, self.result.texts)), prof))
        del self._undo[:-50]

    def undo(self):
        while self._undo and self._undo[-1][0] is not self.result: self._undo.pop()   # from another area
        if not self._undo: self.statusBar().showMessage('Nothing to undo.', 3000); return
        _, (syms, texts), prof = self._undo.pop()
        self.result.symbols, self.result.texts = syms, texts
        if prof and self.profile:
            changed = (len(prof[0]) != len(self.profile.symbols) or prof[1] != self.profile.drop_labels)
            self.profile.symbols, self.profile.drop_labels = list(prof[0]), list(prof[1])
            if changed: self._save_quietly(); self.fill_setup()
        self._sel = None; self.draw_result(); self.fill_review(); self.statusBar().showMessage('Undone.', 3000)

    def change_type(self, i):
        s = getattr(self, '_sel', None)
        if s is None or not hasattr(s, 'cls'): return
        self._snap()
        s.cls = list(SYMBOL_TYPES)[i]; s.review = ''; self._learn_symbol(s, by_user=True); self._after_review_change()

    def delete_sel(self):
        s = getattr(self, '_sel', None)
        if s is None: return
        self._snap()
        if hasattr(s, 'cls'): self.result.symbols.remove(s)
        elif s in self.result.texts:
            self.result.texts.remove(s)
            if s.kind == 'note': self._learn_drop(s)
        self._sel = None; self._after_review_change()

    def ok_sel(self):
        s = getattr(self, '_sel', None)
        if s is None: return
        self._snap()
        if hasattr(s, 'cls') and s.review: self._learn_symbol(s)     # it was unsure: now it knows
        s.review = ''; self._after_review_change()

    # ---------- learning from the review (saved to the builder; undo on the Teach tab / Builder settings)
    def _learn_symbol(self, s, by_user=False):
        """Save the builder's drawing under a reviewed symbol as an example of its (corrected) type."""
        shape = getattr(s, 'shape', '') or ''
        LIGHTS = {'ceiling_light', 'wall_light'}
        if shape and shape != s.cls and not {shape, s.cls} <= LIGHTS:
            # its type came from a note (CLG FAN, 220V, DISK), not its drawing: the drawing is still a <shape>.
            # Teaching it as the new type would make every such symbol that type.
            # (Correcting a symbol it simply misread, e.g. a 'fan' that is really a ceiling light, is still learned.)
            if not by_user or (s.cls in ('fan', 'outlet_240', 'disk_light') and s.score >= 0.85): return
        if not (self.profile and self.sheet and self.sheet.kind == 'pdf' and self.region and self.result and self.result.to_page):
            return
        try:
            if self.teacher is None: self.teacher = Teacher(self.sheet.page, self.region, self.profile)
            x, y = self.result.to_page(s.x, s.y)
            ex, _ = self.teacher.example_at(x, y, s.cls)
            ex.learned = True
            self.profile.symbols.append(ex); self._example_row(ex); self._save_quietly()
            self.statusBar().showMessage(f'Learned: this {sym_label(s.cls).split(" (")[0].lower()} is saved for {self.profile.name}. '
                                         'Remove it on the Teach tab if that was a mistake.', 8000)
        except Exception as e:                         # learning is a bonus: never block the review
            self.statusBar().showMessage(f'Could not learn from that symbol ({e}).', 5000)

    def _learn_drop(self, t):
        """A deleted builder note: leave the same wording off this builder's future plans."""
        if not self.profile: return
        import re as _re
        # one-line notes only: a line of a longer note ('TO LIGHT' / 'ABOVE') is wording other notes share
        if len(t.lines) != 1 or len(_re.sub(r'[^A-Z0-9]', '', t.lines[0].upper())) < 2: return
        new = [p for p in [r'^' + _re.escape(t.lines[0].strip()) + r'$'] if p not in self.profile.drop_labels]
        if not new: return
        self.profile.drop_labels += new; self._save_quietly()
        self.statusBar().showMessage(f'Learned: "{" / ".join(t.lines)}" will be left off {self.profile.name} plans. '
                                     'Undo in Builder settings.', 8000)

    def _save_quietly(self):
        try: self.profile.save(self.settings.profile_path(self.profile.name))
        except Exception: pass

    def _after_review_change(self):
        """Redraw, then move straight on to the next item on the list."""
        row = max(self.review.currentRow(), 0)
        self.draw_result(); self.fill_review()
        if self.review.count():
            self.review.blockSignals(True); self.review.setCurrentRow(min(row, self.review.count() - 1)); self.review.blockSignals(False)
            self.on_pick(self.review.currentItem().data(Qt.UserRole), zoom=True, ask=False)

    # ---------- drag a plan onto the window
    def dragEnterEvent(self, e):
        if _plan_url(e.mimeData()): e.acceptProposedAction(); self._overlay.show_over()

    def dragLeaveEvent(self, e):
        self._overlay.hide()

    def dropEvent(self, e):
        self._overlay.hide()
        fs = _plan_urls(e.mimeData())
        if not fs: return
        if self.plan: self._add_files(fs)          # dropped onto an open plan: more of the builder's files
        else: self.new_cleanup(fs)

    # ---------- saving
    def library_blocks(self):
        lib = self.library()
        return sorted(b.name for b in lib.blocks if not b.name.startswith('*')) if lib else []

    def library(self):
        if self._lib is not None: return self._lib
        p = self.settings.block_library
        if not p or not Path(p).exists(): return None
        try:
            dxf = oda.to_dxf(p, oda.find_converter(self.settings.oda_path))
            self._lib = dxfout.load_library(dxf)
        except Exception as e:
            QMessageBox.warning(self, 'Block library', f'Could not read your block library:\n{e}'); return None
        return self._lib

    def open_settings(self, parent=None):
        d = SettingsDialog(self.settings, self)
        if d.exec():
            old = self.settings.block_library; d.apply()
            if self.settings.block_library != old: self._lib = None; self._shapes = None
            if self.plan: self.fill_items()

    def save_plan_dwg(self):
        """Write every area in the plan into one drawing."""
        if self.result is not None:
            # the area on screen hasn't gone into the plan yet
            what = 'Update area %d in the plan with your changes' % (self.editing + 1) if self.editing is not None else 'Add the area you just cleaned to the plan'
            r = QMessageBox.question(self, 'Save drawing', what + ' before saving?', QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel: return
            if r == QMessageBox.Yes: self.add_to_plan()
        if not (self.plan and self.plan.items):
            QMessageBox.information(self, 'Save drawing', 'Nothing in the plan yet. Clean an area and click Add to plan first.'); return
        n_plan = sum(1 for it in self.plan.items if it.area_type == 'Electrical plan')
        n_opt = sum(1 for it in self.plan.items if it.area_type == 'Option')
        n_el = sum(1 for it in self.plan.items if is_elev(it.area_type))
        tpl = self.settings.master_template
        parts = []
        if n_plan or n_opt:
            parts.append(f'{self.plan.name}.dwg: {n_plan} plan{"s" if n_plan != 1 else ""} and {n_opt} option{"s" if n_opt != 1 else ""}, '
                         + (f'on {Path(tpl).name}' if tpl else 'on a blank drawing (no master template in Settings)'))
        if n_el: parts.append(f'{self.plan.name} Elevations.dwg: {n_el} elevation{"s" if n_el != 1 else ""}')
        box = QMessageBox(QMessageBox.Question, 'Save drawing', 'This makes:\n\n' + '\n'.join('• ' + p for p in parts) +
                          '\n\nYou can keep adding to the plan afterwards and save again.', QMessageBox.Cancel, self)
        go = box.addButton('Save', QMessageBox.AcceptRole); box.setDefaultButton(go); box.exec()
        if box.clickedButton() is not go: return
        try: allres = [self.plan.result(it) for it in self.plan.items]
        except Exception as e: QMessageBox.critical(self, 'Save failed', f'Could not read a saved area:\n{e}'); return
        # plans and options go on the master template; elevations go in their own drawing
        items = [it for it in self.plan.items if not is_elev(it.area_type)]
        results = [r for r, it in zip(allres, self.plan.items) if not is_elev(it.area_type)]
        elevs = [upright(r, it) for r, it in zip(allres, self.plan.items) if is_elev(it.area_type)]
        base = None
        tpl = self.settings.master_template
        if tpl:
            if not Path(tpl).exists():
                if QMessageBox.question(self, 'Template not found', f'The master template isn\'t where Settings says:\n{tpl}\n\n'
                                        'Save on a blank drawing instead? (No: fix it in Settings first.)') != QMessageBox.Yes: return
            else:
                try: base = dxfout.open_template(tpl, oda.find_converter(self.settings.oda_path))
                except Exception as e: QMessageBox.critical(self, 'Template', f'Could not read the master template:\n{e}'); return
        default = str(Path(self.plan.files[0]).parent / f'{self.plan.name}.dwg') if self.plan.files else f'{self.plan.name}.dwg'   # next to the builder's PDF
        res, clear = None, True
        if base is not None:
            board = find_board(base)
            if board:
                plans = [(r, it.elevation, it.level) for r, it in zip(results, items) if it.area_type != 'Option']
                opts = [r for r, it in zip(results, items) if it.area_type == 'Option']
                res, spill = on_board(board, plans, opts, self.settings.text_height_title, self.plan.project, self.plan.name, self.plan.elevations)
                if spill:      # more plans than boxes: they go above the board
                    extra = dxfout._moved_clear_of(combine(spill, self.settings.text_height_title), base)
                    res.lines += extra.lines; res.arcs += extra.arcs; res.symbols += extra.symbols; res.texts += extra.texts
                clear = False
        saved, notes = [], []
        main_path = None
        if results:
            if res is None: res = combine(results, self.settings.text_height_title)
            out = self._write(res, self.plan.dwg_path or default, layout=False, base=base, clear=clear, quiet=True)
            if not out: return
            main_path, missing = out; saved.append(Path(main_path).name)
            self.plan.dwg_path = main_path; self.plan.save()
            if missing: notes.append('These blocks were not in your library, so placeholders were used: ' + ', '.join(missing))
        if elevs:
            # a blank drawing with the template's layers, text and dimension styles
            ebase = None
            if base is not None:     # read fresh: the plans drawing changed the one in memory
                ebase = dxfout.blank_like(dxfout.open_template(tpl, oda.find_converter(self.settings.oda_path)))
            stem = Path(main_path).with_suffix('') if main_path else Path(default).with_suffix('')
            epath = f'{stem} Elevations{Path(main_path or default).suffix}'
            out = self._write(combine(elevs, self.settings.text_height_title), epath, layout=False, base=ebase, clear=False,
                              quiet=True, path=epath if main_path else None)
            if not out: return
            saved.append(Path(out[0]).name)
        if saved:
            exe = oda.find_converter(self.settings.oda_path)
            msg = 'Saved ' + ' and '.join(saved) + '.'
            if notes: msg += '\n\n' + '\n'.join(notes)
            if not exe: msg += '\n\nInstall the free ODA File Converter to save DWG directly.'
            QMessageBox.information(self, 'Saved', msg)

    def save_result(self):
        if not self.result: return
        self._write(self.result, 'cleaned plan.dwg')

    def _write(self, res, default_name, layout=True, base=None, clear=True, quiet=False, path=None):
        """Write one drawing. path: save there without asking. Returns (path, missing blocks), or None."""
        lib = self.library()
        if lib is None:
            if QMessageBox.question(self, 'Block library', 'Your block library is not set, so symbols will be placeholders. '
                                    'Set it now in Settings?') == QMessageBox.Yes:
                self.open_settings(); lib = self.library()
        exe = oda.find_converter(self.settings.oda_path)
        flt = 'AutoCAD drawing (*.dwg);;DXF (*.dxf)' if exe else 'DXF (*.dxf)'
        if not exe: default_name = str(Path(default_name).with_suffix('.dxf'))
        if path: f, chosen = (str(Path(path).with_suffix('.dxf')) if not exe else path), ('dwg' if exe else 'dxf')
        else: f, chosen = QFileDialog.getSaveFileName(self, 'Save drawing', default_name, flt)
        if not f: return None
        want_dwg = f.lower().endswith('.dwg') or (exe and 'dwg' in chosen and not f.lower().endswith('.dxf'))
        tmp = Path(tempfile.mkdtemp()) / 'cleaned.dxf'
        missing = dxfout.write_dxf(res, str(tmp), lib, self.settings.block_for,
                                   self.settings.text_height_room, self.settings.text_height_note,
                                   self.settings.text_layer, self.settings.symbol_layer, self.settings.text_style,
                                   self.settings.text_height_title, layout=layout, base=base, clear=clear)
        try:
            if want_dwg:
                if not f.lower().endswith('.dwg'): f += '.dwg'
                oda.convert(tmp, f, exe)
            else:
                import shutil; shutil.copy(tmp, f)
        except Exception as e:
            QMessageBox.critical(self, 'Save failed', str(e)); return None
        if quiet: return f, missing
        msg = f'Saved {Path(f).name}.'
        if missing: msg += '\n\nThese blocks were not in your library, so placeholders were used: ' + ', '.join(missing)
        if not exe: msg += '\n\nInstall the free ODA File Converter to save DWG directly.'
        QMessageBox.information(self, 'Saved', msg)

def main():
    app = QApplication(sys.argv); app.setApplicationName('Plan Cleanup')
    from . import style; style.apply(app)
    w = MainWindow(); w.showMaximized()          # fill the screen (not macOS full-screen mode)
    if len(sys.argv) > 1: w.load(sys.argv[1:])
    else: QTimer.singleShot(200, w.new_cleanup)    # start with the New cleanup window
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
