"""The 'New cleanup' window: pick the builder's plan, the builder, and the sheet."""
from __future__ import annotations
from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QListWidget,
                               QListWidgetItem, QFileDialog, QInputDialog, QListView, QFrame)
from planclean.profile import Profile, safe_name
from planclean import oda

THUMB = 150

def _heading(n, text):
    l = QLabel(f'<span style="color:#0a7cff;font-weight:600">{n}</span>&nbsp;&nbsp;<b>{text}</b>')
    l.setStyleSheet('font-size:15px;margin-top:10px'); return l

def _big(text, primary=False):
    b = QPushButton(text); b.setMinimumHeight(38)
    b.setStyleSheet(('QPushButton{background:#0a7cff;color:white;border:none;border-radius:6px;padding:6px 18px;font-weight:600}'
                     'QPushButton:disabled{background:#b7d4f5}') if primary else
                    'QPushButton{border:1px solid #c8ccd2;border-radius:6px;padding:6px 14px;background:white}')
    return b

class StartDialog(QDialog):
    def __init__(self, settings, parent=None, open_settings=None):
        super().__init__(parent)
        self.setWindowTitle('New cleanup'); self.settings = settings; self.open_settings = open_settings
        self.path = None; self.doc = None; self.sheet_index = 0
        self.setAcceptDrops(True)
        v = QVBoxLayout(self); v.setContentsMargins(24, 18, 24, 18)
        t = QLabel('New cleanup'); t.setStyleSheet('font-size:22px;font-weight:700'); v.addWidget(t)
        v.addWidget(QLabel('Choose the builder\'s plan, who the builder is, and which sheet to clean.'))

        v.addWidget(_heading('1', 'Builder\'s plan (PDF or DWG)'))
        row = QHBoxLayout(); self.file_btn = _big('Choose plan file…'); self.file_btn.clicked.connect(self.choose_file)
        self.file_lbl = QLabel('or drag a file onto this window'); self.file_lbl.setStyleSheet('color:#666')
        row.addWidget(self.file_btn); row.addWidget(self.file_lbl, 1); v.addLayout(row)

        v.addWidget(_heading('2', 'Builder'))
        row = QHBoxLayout(); self.builder = QComboBox(); self.builder.setMinimumHeight(32); self.builder.setMinimumWidth(260)
        self.builder.currentIndexChanged.connect(self.update_state)
        nb = _big('New builder…'); nb.clicked.connect(self.new_builder)
        row.addWidget(self.builder); row.addWidget(nb); row.addStretch(); v.addLayout(row)
        self.fill_builders()

        self.sheet_head = _heading('3', 'Sheet: click the electrical plan'); v.addWidget(self.sheet_head)
        self.sheets = QListWidget(); self.sheets.setViewMode(QListView.IconMode); self.sheets.setIconSize(QSize(THUMB, THUMB))
        self.sheets.setResizeMode(QListView.Adjust); self.sheets.setMovement(QListView.Static); self.sheets.setSpacing(6)
        self.sheets.setMinimumHeight(330); self.sheets.currentRowChanged.connect(self.update_state)
        self.sheets.itemDoubleClicked.connect(lambda _: self.start.isEnabled() and self.accept())
        v.addWidget(self.sheets, 1)

        self.warn = QLabel(); self.warn.setWordWrap(True); self.warn.setTextFormat(Qt.RichText)
        self.warn.setStyleSheet('background:#fff6e0;border:1px solid #f0d48a;border-radius:6px;padding:8px')
        self.warn_btn = _big('Open Settings'); self.warn_btn.clicked.connect(self.fix_settings)
        row = QHBoxLayout(); row.addWidget(self.warn, 1); row.addWidget(self.warn_btn); v.addLayout(row)

        row = QHBoxLayout(); row.addStretch()
        c = _big('Cancel'); c.clicked.connect(self.reject); row.addWidget(c)
        self.start = _big('Start', primary=True); self.start.clicked.connect(self.accept); row.addWidget(self.start)
        v.addLayout(row)
        self._thumb_queue = []; self._timer = QTimer(self); self._timer.timeout.connect(self._next_thumb)
        self.resize(900, 780); self.check_setup(); self.update_state()

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
        self.builder.blockSignals(False); self.update_state() if hasattr(self, 'start') else None

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
        self.path = f; self.file_lbl.setText(Path(f).name); self.file_lbl.setStyleSheet('color:#111;font-weight:600')
        self.sheets.clear(); self._timer.stop(); self.doc = None
        is_pdf = f.lower().endswith('.pdf')
        self.sheet_head.setVisible(is_pdf); self.sheets.setVisible(is_pdf)
        if is_pdf:
            self.doc = pymupdf.open(f)
            blank = QPixmap(THUMB, int(THUMB * 0.65)); blank.fill(Qt.white)
            for i in range(len(self.doc)):
                it = QListWidgetItem(QIcon(blank), f'Page {i + 1}'); it.setSizeHint(QSize(THUMB + 16, THUMB + 24)); self.sheets.addItem(it)
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

    def update_state(self, *_):
        is_pdf = bool(self.path and self.path.lower().endswith('.pdf'))
        ok = bool(self.path and self.builder_name() and (not is_pdf or self.sheets.currentRow() >= 0))
        self.start.setEnabled(ok)
        self.sheet_index = max(self.sheets.currentRow(), 0)
