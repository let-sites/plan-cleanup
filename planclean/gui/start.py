"""The 'New cleanup' window: pick the builder's plan, the builder, and the sheet."""
from __future__ import annotations
from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont
from PySide6.QtWidgets import (QStackedWidget, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QListWidget,
                               QListWidgetItem, QFileDialog, QInputDialog, QListView, QFrame)
from planclean.profile import Profile, safe_name
from planclean import oda

THUMB = 260

def _heading(n, text):
    l = QLabel(f'<span style="color:#4a90b8;font-weight:700">{n}</span>&nbsp;&nbsp;<b>{text}</b>')
    l.setStyleSheet('font-size:16px;margin-top:12px'); return l

def _big(text, primary=False):
    b = QPushButton(text); b.setMinimumHeight(38); b.setProperty('kind', 'primary' if primary else 'big')
    return b

class StartDialog(QDialog):
    def __init__(self, settings, parent=None, open_settings=None):
        super().__init__(parent)
        self.setWindowTitle('New cleanup'); self.settings = settings; self.open_settings = open_settings
        self.path = None; self.doc = None; self.sheet_index = 0
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(); outer.addWidget(self.stack)

        # ---- page 1: plan file and builder
        p1 = QWidget(); v = QVBoxLayout(p1); v.setContentsMargins(32, 26, 32, 22)
        t = QLabel('New cleanup'); t.setStyleSheet('font-size:26px;font-weight:700'); v.addWidget(t)
        sub = QLabel('Choose the builder\'s plan and who the builder is.'); sub.setProperty('kind', 'muted'); v.addWidget(sub)
        v.addWidget(_heading('1', 'Builder\'s plan (PDF or DWG)'))
        drop = QFrame(); drop.setStyleSheet('QFrame{background:white;border:2px dashed #c9d6de;border-radius:10px}')
        dv = QVBoxLayout(drop); dv.setContentsMargins(20, 22, 20, 22)
        self.file_btn = _big('Choose plan file…', primary=True); self.file_btn.clicked.connect(self.choose_file)
        self.file_lbl = QLabel('or drag the file here'); self.file_lbl.setAlignment(Qt.AlignCenter); self.file_lbl.setStyleSheet('border:none;color:#6b7a86')
        row = QHBoxLayout(); row.addStretch(); row.addWidget(self.file_btn); row.addStretch(); dv.addLayout(row); dv.addWidget(self.file_lbl)
        v.addWidget(drop)
        v.addWidget(_heading('2', 'Builder'))
        row = QHBoxLayout(); self.builder = QComboBox(); self.builder.setMinimumWidth(300)
        self.builder.currentIndexChanged.connect(self.update_state)
        nb = _big('New builder…'); nb.clicked.connect(self.new_builder)
        row.addWidget(self.builder); row.addWidget(nb); row.addStretch(); v.addLayout(row)
        self.fill_builders()
        v.addStretch()
        self.warn = QLabel(); self.warn.setWordWrap(True); self.warn.setTextFormat(Qt.RichText)
        self.warn.setStyleSheet('background:#fff8e6;border:1px solid #f0d48a;border-radius:8px;padding:10px')
        self.warn_btn = _big('Open Settings'); self.warn_btn.clicked.connect(self.fix_settings)
        row = QHBoxLayout(); row.addWidget(self.warn, 1); row.addWidget(self.warn_btn); v.addLayout(row)
        row = QHBoxLayout(); row.addStretch()
        c = _big('Cancel'); c.clicked.connect(self.reject); row.addWidget(c)
        self.next = _big('Next', primary=True); self.next.clicked.connect(self.go_next); row.addWidget(self.next)
        v.addLayout(row)
        self.stack.addWidget(p1)

        # ---- page 2: pick the sheet, with big previews
        p2 = QWidget(); v = QVBoxLayout(p2); v.setContentsMargins(32, 26, 32, 22)
        self.p2_title = QLabel('Which sheet?'); self.p2_title.setStyleSheet('font-size:26px;font-weight:700'); v.addWidget(self.p2_title)
        sub = QLabel('Click the electrical plan you want to clean, then Open. Double-click opens it straight away.'); sub.setProperty('kind', 'muted'); v.addWidget(sub)
        self.sheets = QListWidget(); self.sheets.setViewMode(QListView.IconMode); self.sheets.setIconSize(QSize(THUMB, THUMB))
        self.sheets.setResizeMode(QListView.Adjust); self.sheets.setMovement(QListView.Static); self.sheets.setSpacing(10)
        self.sheets.setUniformItemSizes(True)
        self.sheets.currentRowChanged.connect(self.update_state)
        self.sheets.itemDoubleClicked.connect(lambda _: self.start.isEnabled() and self.accept())
        v.addWidget(self.sheets, 1)
        row = QHBoxLayout()
        back = _big('Back'); back.clicked.connect(lambda: self.stack.setCurrentIndex(0)); row.addWidget(back); row.addStretch()
        c = _big('Cancel'); c.clicked.connect(self.reject); row.addWidget(c)
        self.start = _big('Open', primary=True); self.start.clicked.connect(self.accept); row.addWidget(self.start)
        v.addLayout(row)
        self.stack.addWidget(p2)
        self.sheet_head = sub
        self._thumb_queue = []; self._timer = QTimer(self); self._timer.timeout.connect(self._next_thumb)
        self.resize(1100, 820); self.check_setup(); self.update_state()

    # ----- setup checks
    def check_setup(self):
        probs = []
        if not (self.settings.block_library and Path(self.settings.block_library).exists()):
            probs.append('Choose your <b>block library</b> (one of your own DWG plans) in Settings.')
        if not oda.find_converter(self.settings.oda_path):
            probs.append('Install the free <b>ODA File Converter</b> (search "ODA File Converter download"). '
                         'It lets the app read and save DWG files.')
        self.warn.setText('<b>Before your first cleanup:</b><br>' + '<br>'.join('• ' + p for p in probs) if probs else '')
        self.warn.setVisible(bool(probs)); self.warn_btn.setVisible(bool(probs))

    def fix_settings(self):
        if self.open_settings: self.open_settings(self)
        self.check_setup(); self.fill_builders(self.builder.currentText())

    # ----- builders
    def fill_builders(self, select=None):
        self.builder.blockSignals(True); self.builder.clear(); self.builder.addItem('Choose builder…')
        for n in self.settings.list_profiles(): self.builder.addItem(n)
        if select and self.builder.findText(select) > 0: self.builder.setCurrentIndex(self.builder.findText(select))
        self.builder.blockSignals(False); self.update_state()

    def new_builder(self):
        name, ok = QInputDialog.getText(self, 'New builder', 'Builder name:')
        if ok and name.strip():
            p = Profile(name=name.strip()); p.save(self.settings.profile_path(p.name))
            self.fill_builders(safe_name(p.name))

    def builder_name(self):
        return self.builder.currentText() if self.builder.currentIndex() > 0 else None

    # ----- file
    def choose_file(self):
        f, _ = QFileDialog.getOpenFileName(self, 'Choose the builder\'s plan', '', 'Plans (*.pdf *.dwg)')
        if f: self.set_file(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(('.pdf', '.dwg', '.dxf')): self.set_file(u.toLocalFile()); break

    def set_file(self, f):
        self.path = f; self.file_lbl.setText(Path(f).name); self.file_lbl.setStyleSheet('border:none;font-weight:600;font-size:15px')
        self.sheets.clear(); self._timer.stop(); self.doc = None
        is_pdf = f.lower().endswith('.pdf')
        if is_pdf:
            self.doc = pymupdf.open(f)
            blank = QPixmap(THUMB, int(THUMB * 0.65)); blank.fill(Qt.white)
            for i in range(len(self.doc)):
                it = QListWidgetItem(QIcon(blank), f'Page {i + 1}'); it.setSizeHint(QSize(THUMB + 20, int(THUMB * 0.72) + 34)); self.sheets.addItem(it)
            self._thumb_queue = list(range(len(self.doc))); self._timer.start(0)
        self.update_state()

    def _next_thumb(self):
        if not self._thumb_queue or self.doc is None: self._timer.stop(); return
        i = self._thumb_queue.pop(0); page = self.doc[i]
        z = THUMB / max(page.rect.width, page.rect.height)
        pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888).copy()
        it = self.sheets.item(i)
        if it: it.setIcon(QIcon(QPixmap.fromImage(img)))

    def is_pdf(self):
        return bool(self.path and self.path.lower().endswith('.pdf'))

    def update_state(self, *_):
        if not hasattr(self, 'start'): return
        self.next.setEnabled(bool(self.path and self.builder_name()))
        self.next.setText('Next' if (self.is_pdf() or not self.path) else 'Open')
        self.start.setEnabled(self.sheets.currentRow() >= 0)
        self.sheet_index = max(self.sheets.currentRow(), 0)

    def go_next(self):
        if not self.is_pdf(): self.accept(); return
        self.p2_title.setText(f'Which sheet in {Path(self.path).name}?')
        self.stack.setCurrentIndex(1)
