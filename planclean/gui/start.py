"""The 'New cleanup' window: pick the builder's plan, the builder, and the sheet."""
from __future__ import annotations
import re
from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont
from PySide6.QtWidgets import (QStackedWidget, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QListWidget,
                               QListWidgetItem, QFileDialog, QInputDialog, QListView, QFrame, QRadioButton, QCheckBox, QButtonGroup,
                               QDialogButtonBox, QMessageBox, QLineEdit)
from planclean.profile import Profile, safe_name
from planclean import oda

THUMB = 260
PLAN_EXTS = ('.pdf', '.dwg', '.dxf')

def _plan_urls(mime):
    """Every dragged file that is a plan."""
    out = []
    if not mime.hasUrls(): return out
    for u in mime.urls():
        f = u.toLocalFile()
        if f.lower().endswith(PLAN_EXTS): out.append(f); continue
        try:
            with open(f, 'rb') as fh:
                if fh.read(5) == b'%PDF-': out.append(f)      # a PDF saved without its .pdf ending
        except OSError: pass
    return out

def _plan_url(mime):
    """The first dragged file that is a plan, or None."""
    u = _plan_urls(mime); return u[0] if u else None


class OpenPlanDialog(QDialog):
    """Your saved Plans, newest first: open one, or delete ones you're done with."""
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle('Open a saved plan'); self.folder = None
        from planclean.plan import Plan
        self.Plan = Plan
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 18)
        t = QLabel('Saved plans'); t.setStyleSheet('font-size:22px;font-weight:700'); v.addWidget(t)
        self.lst = QListWidget(); self.lst.itemDoubleClicked.connect(lambda _: self.open_sel()); v.addWidget(self.lst, 1)
        row = QHBoxLayout()
        d = QPushButton('Delete…'); d.clicked.connect(self.delete); row.addWidget(d); row.addStretch()
        c = _big('Cancel'); c.clicked.connect(self.reject); row.addWidget(c)
        self.ok = _big('Open', primary=True); self.ok.clicked.connect(self.open_sel); row.addWidget(self.ok)
        v.addLayout(row); self.refresh(); self.resize(560, 480)

    def refresh(self):
        import time
        self.lst.clear()
        for name, folder, mt in self.Plan.list_saved():
            it = QListWidgetItem(f'{name}    ·    {time.strftime("%b %d, %Y %I:%M %p", time.localtime(mt))}')
            it.setData(Qt.UserRole, folder); it.setSizeHint(QSize(100, 34)); self.lst.addItem(it)
        if self.lst.count(): self.lst.setCurrentRow(0)
        self.ok.setEnabled(self.lst.count() > 0)

    def open_sel(self):
        it = self.lst.currentItem()
        if it: self.folder = it.data(Qt.UserRole); self.accept()

    def delete(self):
        it = self.lst.currentItem()
        if not it: return
        name = it.text().split('    ·')[0]
        box = QMessageBox(QMessageBox.Warning, 'Delete plan', f'Delete the saved plan "{name}"?\n\n'
                          'This removes the cleaned areas saved with it. The builder\'s PDFs and any DWG you saved are not touched.', parent=self)
        yes = box.addButton('Delete', QMessageBox.DestructiveRole); box.addButton(QMessageBox.Cancel); box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is yes:
            import shutil; shutil.rmtree(it.data(Qt.UserRole), ignore_errors=True); self.refresh()

class DropOverlay(QLabel):
    """A blue sheet over a window while a plan file is dragged onto it."""
    def __init__(self, parent):
        super().__init__('Drop the plan here', parent)
        self.setAlignment(Qt.AlignCenter); self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet('background:rgba(74,144,184,150);color:white;font-size:28px;font-weight:700;'
                           'border:4px dashed white;border-radius:14px')
        self.hide()
    def show_over(self):
        self.setGeometry(self.parentWidget().rect().adjusted(8, 8, -8, -8)); self.raise_(); self.show()

OUTLET_CHOICES = [('all', 'All outlets'), ('outside', 'Only exterior and garage outlets'), ('none', 'No outlets')]

class BuilderSettingsDialog(QDialog):
    """What LET's finished plan shows for one builder."""
    def __init__(self, profile, parent=None):
        super().__init__(parent); self.profile = profile
        self.setWindowTitle(f'Builder settings: {profile.name}')
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 16)
        t = QLabel(f'What to show on {profile.name} plans'); t.setStyleSheet('font-size:18px;font-weight:700'); v.addWidget(t)
        v.addWidget(QLabel('<b>Outlets</b>'))
        self.out_group = QButtonGroup(self)
        for i, (key, text) in enumerate(OUTLET_CHOICES):
            r = QRadioButton(text); r.setChecked(profile.outlets == key); self.out_group.addButton(r, i); v.addWidget(r)
        hint = QLabel('Exterior means outlets marked WP or nearest a patio, porch or deck. Garage means nearest the garage label.')
        hint.setWordWrap(True); hint.setProperty('kind', 'muted'); v.addWidget(hint)
        v.addWidget(QLabel('<b>Other symbols</b>'))
        self.det = QCheckBox('Smoke and smoke/CO detectors'); self.det.setChecked(profile.detectors); v.addWidget(self.det)
        self.fan = QCheckBox('Exhaust fans (and their wires)'); self.fan.setChecked(profile.exhaust_fans); v.addWidget(self.fan)
        # notes deleted during review are left off from then on: list them so a mistake can be undone
        from planclean.profile import DEFAULT_DROP
        self.learned = [p for p in profile.drop_labels if p not in DEFAULT_DROP]
        if self.learned:
            v.addWidget(QLabel('<b>Builder notes left off</b> (learned when you deleted them)'))
            self.drop_list = QListWidget(); self.drop_list.setMaximumHeight(140)
            for pat in self.learned:
                it = QListWidgetItem(re.sub(r'\\(.)', r'\1', pat.strip('^$'))); it.setData(Qt.UserRole, pat); self.drop_list.addItem(it)
            v.addWidget(self.drop_list)
            rm = QPushButton('Show it again'); rm.setToolTip('Stop leaving the selected note off')
            rm.clicked.connect(lambda: [self.drop_list.takeItem(self.drop_list.row(i)) for i in self.drop_list.selectedItems()])
            v.addWidget(rm, 0, Qt.AlignLeft)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); v.addSpacing(8); v.addWidget(bb)

    def accept(self):
        self.profile.outlets = OUTLET_CHOICES[max(self.out_group.checkedId(), 0)][0]
        self.profile.detectors = self.det.isChecked(); self.profile.exhaust_fans = self.fan.isChecked()
        if self.learned:
            keep = {self.drop_list.item(i).data(Qt.UserRole) for i in range(self.drop_list.count())}
            self.profile.drop_labels = [p for p in self.profile.drop_labels if p not in self.learned or p in keep]
        super().accept()

def edit_builder_settings(settings, name, parent=None):
    """Open the settings for a saved builder and save them. True if saved."""
    path = settings.profile_path(name)
    p = Profile.load(path)
    if BuilderSettingsDialog(p, parent).exec() != QDialog.Accepted: return False
    p.save(path); return True

class ManageBuildersDialog(QDialog):
    """Every saved builder: archive the ones you're done with (hidden, kept), restore them, or delete them."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings
        self.setWindowTitle('Builders'); self.resize(520, 520)
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 16)
        t = QLabel('Builders'); t.setStyleSheet('font-size:18px;font-weight:700'); v.addWidget(t)
        v.addWidget(QLabel('<b>In use</b>'))
        self.active = QListWidget(); self.active.setSelectionMode(QListWidget.ExtendedSelection); v.addWidget(self.active, 2)
        row = QHBoxLayout()
        arch = QPushButton('Archive'); arch.setToolTip('Hide from the builder list. Nothing is lost; restore it any time.')
        dele = QPushButton('Delete…'); dele.setToolTip('Delete the builder and everything taught for it')
        arch.clicked.connect(self.archive); dele.clicked.connect(lambda: self.delete(self.active, self.settings.profiles_dir()))
        row.addWidget(arch); row.addWidget(dele); row.addStretch(); v.addLayout(row)
        v.addWidget(QLabel('<b>Archived</b>'))
        self.archived = QListWidget(); self.archived.setSelectionMode(QListWidget.ExtendedSelection); v.addWidget(self.archived, 1)
        row = QHBoxLayout()
        rest = QPushButton('Restore'); rest.clicked.connect(self.restore)
        dele2 = QPushButton('Delete…'); dele2.clicked.connect(lambda: self.delete(self.archived, self.archive_dir()))
        row.addWidget(rest); row.addWidget(dele2); row.addStretch(); v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(self.reject); bb.accepted.connect(self.accept)
        v.addSpacing(8); v.addWidget(bb)
        self.refresh()

    def archive_dir(self):
        d = self.settings.profiles_dir() / 'Archived'; d.mkdir(exist_ok=True); return d

    def refresh(self):
        self.active.clear(); self.archived.clear()
        for n in self.settings.list_profiles(): self.active.addItem(n)
        for f in sorted(self.archive_dir().glob('*.json')): self.archived.addItem(f.stem)

    def _move(self, lst, src, dst):
        for it in lst.selectedItems():
            f = src / (it.text() + '.json'); target = dst / f.name
            if target.exists():
                QMessageBox.warning(self, 'Builders', f'There is already a builder called "{it.text()}" there.'); continue
            f.replace(target)
        self.refresh()

    def archive(self): self._move(self.active, self.settings.profiles_dir(), self.archive_dir())
    def restore(self): self._move(self.archived, self.archive_dir(), self.settings.profiles_dir())

    def delete(self, lst, folder):
        names = [it.text() for it in lst.selectedItems()]
        if not names: return
        msg = ('Delete ' + (f'"{names[0]}"' if len(names) == 1 else f'these {len(names)} builders') +
               ' and everything taught for ' + ('it' if len(names) == 1 else 'them') + '? This can\'t be undone.\n\n'
               'To keep a builder but hide it from the list, use Archive instead.')
        box = QMessageBox(QMessageBox.Warning, 'Delete builder', msg, QMessageBox.Cancel, self)
        yes = box.addButton('Delete', QMessageBox.DestructiveRole); box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not yes: return
        for n in names:
            try: (folder / (n + '.json')).unlink()
            except OSError: pass
        self.refresh()

def _heading(n, text):
    l = QLabel(f'<span style="color:#4a90b8;font-weight:700">{n}</span>&nbsp;&nbsp;<b>{text}</b>')
    l.setStyleSheet('font-size:16px;margin-top:12px'); return l

def _big(text, primary=False):
    b = QPushButton(text); b.setMinimumHeight(38); b.setProperty('kind', 'primary' if primary else 'big')
    return b

class StartDialog(QDialog):
    def __init__(self, settings, parent=None, open_settings=None):
        super().__init__(parent)
        self.setWindowTitle('New plan'); self.settings = settings; self.open_settings = open_settings
        self.path = None; self.paths = []; self.doc = None; self.sheet_index = 0; self.open_folder = None; self._named = False
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(); outer.addWidget(self.stack)

        # ---- page 1: plan file and builder
        p1 = QWidget(); v = QVBoxLayout(p1); v.setContentsMargins(32, 26, 32, 22)
        t = QLabel('New plan'); t.setStyleSheet('font-size:26px;font-weight:700'); v.addWidget(t)
        sub = QLabel('Name it, add the builder\'s files, and choose the builder. Everything you clean goes into one drawing, '
                     'and the plan is saved so you can come back to it.'); sub.setProperty('kind', 'muted'); sub.setWordWrap(True); v.addWidget(sub)
        v.addWidget(_heading('1', 'Plan name (also the drawing\'s file name)'))
        self.plan_name = QLineEdit(); self.plan_name.setPlaceholderText('e.g. C405 Electrical'); self.plan_name.setMinimumHeight(34)
        self.plan_name.textEdited.connect(lambda _: setattr(self, '_named', True)); self.plan_name.textChanged.connect(self.update_state)
        v.addWidget(self.plan_name)
        row = QHBoxLayout()
        self.project = QLineEdit(); self.project.setPlaceholderText('Project name (e.g. the community)'); self.project.setMinimumHeight(30)
        self.elevations = QLineEdit(); self.elevations.setPlaceholderText('Elevation names, e.g. Modern, Traditional, Farmhouse'); self.elevations.setMinimumHeight(30)
        row.addWidget(self.project, 1); row.addWidget(self.elevations, 2); v.addLayout(row)
        v.addWidget(_heading('2', 'Builder\'s files (PDF or DWG, one or several)'))
        self.drop = drop = QFrame(); self._drop_style(False)
        dv = QVBoxLayout(drop); dv.setContentsMargins(20, 22, 20, 22)
        self.name_lbl = QLabel(); self.name_lbl.setAlignment(Qt.AlignCenter); self.name_lbl.setWordWrap(True)
        self.name_lbl.setStyleSheet('border:none;background:transparent;font-weight:700;font-size:17px'); self.name_lbl.hide()
        dv.addWidget(self.name_lbl)
        self.file_btn = _big('Choose files…', primary=True); self.file_btn.clicked.connect(self.choose_file)
        self.file_lbl = QLabel('or drag them here'); self.file_lbl.setAlignment(Qt.AlignCenter); self.file_lbl.setStyleSheet('border:none;background:transparent;color:#6b7a86')
        self.clear_btn = QPushButton('Clear'); self.clear_btn.clicked.connect(self.clear_files); self.clear_btn.hide()
        row = QHBoxLayout(); row.addStretch(); row.addWidget(self.file_btn); row.addWidget(self.clear_btn); row.addStretch(); dv.addLayout(row); dv.addWidget(self.file_lbl)
        v.addWidget(drop)
        v.addWidget(_heading('3', 'Builder'))
        row = QHBoxLayout(); self.builder = QComboBox(); self.builder.setMinimumWidth(300)
        self.builder.currentIndexChanged.connect(self.update_state)
        nb = _big('New builder…'); nb.clicked.connect(self.new_builder)
        self.bset = _big('Builder settings…'); self.bset.clicked.connect(self.builder_settings)
        mb = _big('Builders…'); mb.setToolTip('See every builder; archive or delete ones you no longer need')
        mb.clicked.connect(self.manage_builders)
        row.addWidget(self.builder); row.addWidget(nb); row.addWidget(self.bset); row.addWidget(mb); row.addStretch(); v.addLayout(row)
        self.fill_builders()
        self.template = ''          # the master template is a setting (Settings), read at every save
        v.addStretch()
        self.warn = QLabel(); self.warn.setWordWrap(True); self.warn.setTextFormat(Qt.RichText)
        self.warn.setStyleSheet('background:#fff8e6;border:1px solid #f0d48a;border-radius:8px;padding:10px')
        self.warn_btn = _big('Open Settings'); self.warn_btn.clicked.connect(self.fix_settings)
        row = QHBoxLayout(); row.addWidget(self.warn, 1); row.addWidget(self.warn_btn); v.addLayout(row)
        row = QHBoxLayout()
        op = _big('Open a saved plan…'); op.clicked.connect(self.open_saved); row.addWidget(op); row.addStretch()
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

    def manage_builders(self):
        keep = self.builder_name()
        ManageBuildersDialog(self.settings, self).exec()
        self.fill_builders(keep)

    def new_builder(self):
        name, ok = QInputDialog.getText(self, 'New builder', 'Builder name:')
        if ok and name.strip():
            p = Profile(name=name.strip())
            BuilderSettingsDialog(p, self).exec()          # what this builder's plans should show
            p.save(self.settings.profile_path(p.name))
            self.fill_builders(safe_name(p.name))

    def builder_settings(self):
        if self.builder_name(): edit_builder_settings(self.settings, self.builder_name(), self)

    def builder_name(self):
        return self.builder.currentText() if self.builder.currentIndex() > 0 else None

    # ----- file
    def choose_file(self):
        fs, _ = QFileDialog.getOpenFileNames(self, 'Choose the builder\'s files', '', 'Plans (*.pdf *.dwg)')
        for f in fs: self.set_file(f)

    def open_saved(self):
        d = OpenPlanDialog(self)
        if d.exec() == QDialog.Accepted and d.folder: self.open_folder = d.folder; self.accept()

    def clear_files(self):
        self.paths = []; self.path = None; self.name_lbl.hide(); self.file_lbl.show(); self.clear_btn.hide()
        self.file_btn.setText('Choose files…'); self.update_state()

    def _drop_style(self, active):
        """The dotted drop box turns blue while a plan is dragged over the window."""
        if active:
            self.drop.setStyleSheet('QFrame{background:rgba(74,144,184,60);border:2px dashed #4a90b8;border-radius:10px}')
        else:
            self.drop.setStyleSheet('QFrame{background:white;border:2px dashed #c9d6de;border-radius:10px}')

    def dragEnterEvent(self, e):
        if _plan_url(e.mimeData()): e.acceptProposedAction(); self._drop_style(True)

    def dragLeaveEvent(self, e):
        self._drop_style(False)

    def dropEvent(self, e):
        self._drop_style(False)
        for f in _plan_urls(e.mimeData()): self.set_file(f)

    def set_file(self, f):
        if f in self.paths: return
        self.paths.append(f); self.path = self.paths[0]
        if not self._named: self.plan_name.setText(Path(self.paths[0]).stem)
        # the file names take the top spot; the button becomes "Add more files…" underneath
        self.name_lbl.setText('<br>'.join(Path(p).name for p in self.paths)); self.name_lbl.show(); self.file_lbl.hide(); self.clear_btn.show()
        self.file_btn.setText('Add more files…'); self.file_btn.setProperty('kind', 'big'); self.file_btn.setMinimumHeight(28)
        self.file_btn.style().unpolish(self.file_btn); self.file_btn.style().polish(self.file_btn)
        self.sheets.clear(); self._timer.stop(); self.doc = None
        try:
            with open(f, 'rb') as fh: is_pdf = fh.read(5) == b'%PDF-'
        except OSError: is_pdf = f.lower().endswith('.pdf')
        if is_pdf:
            self.doc = pymupdf.open(f)
            blank = QPixmap(THUMB, int(THUMB * 0.65)); blank.fill(Qt.white)
            for i in range(len(self.doc)):
                it = QListWidgetItem(QIcon(blank), f'Page {i + 1}'); it.setSizeHint(QSize(THUMB + 20, int(THUMB * 0.72) + 34)); self.sheets.addItem(it)
            self._thumb_queue = []
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
        self.next.setEnabled(bool(self.paths and self.builder_name() and self.plan_name.text().strip())); self.bset.setEnabled(bool(self.builder_name()))
        self.next.setText('Open')
        self.start.setEnabled(self.sheets.currentRow() >= 0)
        self.sheet_index = max(self.sheets.currentRow(), 0)

    def go_next(self):
        self.accept()      # the sheet is picked in the main window's sheet list
