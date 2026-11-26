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
        for name, folder, mt, builder in self.Plan.list_saved():
            it = QListWidgetItem(f'{name}    ·    {builder + "    ·    " if builder else ""}{time.strftime("%b %d, %Y %I:%M %p", time.localtime(mt))}')
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

SCALES = [('1/4" = 1\'-0"', 48), ('1/8" = 1\'-0"', 96), ('3/16" = 1\'-0"', 64), ('3/8" = 1\'-0"', 32), ('1/2" = 1\'-0"', 24)]
OUTLET_CHOICES = [('all', 'All outlets'), ('outside', 'Only exterior and garage outlets'), ('none', 'No outlets')]

class BuilderOptions(QWidget):
    """What LET's finished plan shows for one builder. changed() is called after every change (already applied)."""
    def __init__(self, profile, parent=None, changed=None):
        super().__init__(parent); self.profile = profile; self.changed = changed
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout(); row.addWidget(QLabel('<b>Drawing scale</b>'))
        self.scale = QComboBox()
        for n, k in SCALES: self.scale.addItem(n, k)
        cur = round(profile.inches_per_pt * 72); i = self.scale.findData(cur)
        if i < 0: self.scale.addItem(f'{cur}:1', cur); i = self.scale.count() - 1
        self.scale.setCurrentIndex(i); row.addWidget(self.scale); row.addStretch(); v.addLayout(row)
        v.addWidget(QLabel('<b>Outlets</b>'))
        self.out_group = QButtonGroup(self)
        for i, (key, text) in enumerate(OUTLET_CHOICES):
            r = QRadioButton(text); r.setChecked(profile.outlets == key); self.out_group.addButton(r, i); v.addWidget(r)
        hint = QLabel('Exterior means outlets marked WP or nearest a patio, porch or deck. Garage means nearest the garage label.')
        hint.setWordWrap(True); hint.setProperty('kind', 'muted'); v.addWidget(hint)
        v.addWidget(QLabel('<b>Other symbols</b>'))
        self.det = QCheckBox('Smoke and smoke/CO detectors'); self.det.setChecked(profile.detectors); v.addWidget(self.det)
        self.fan = QCheckBox('Exhaust fans (and their wires)'); self.fan.setChecked(profile.exhaust_fans); v.addWidget(self.fan)
        self.opt = QCheckBox('Optional items labeled OPT. (like an OPT. PRE-WIRE fan)'); self.opt.setChecked(getattr(profile, 'optional_items', False)); v.addWidget(self.opt)
        # notes deleted during review are left off from then on: list them so a mistake can be undone
        from planclean.profile import DEFAULT_DROP
        self.learned = [p for p in profile.drop_labels if p not in DEFAULT_DROP]
        self.drop_list = None
        if self.learned:
            v.addWidget(QLabel('<b>Builder notes left off</b> (learned when you deleted them)'))
            self.drop_list = QListWidget(); self.drop_list.setMaximumHeight(140)
            for pat in self.learned:
                it = QListWidgetItem(re.sub(r'\\(.)', r'\1', pat.strip('^$'))); it.setData(Qt.UserRole, pat); self.drop_list.addItem(it)
            v.addWidget(self.drop_list)
            rm = QPushButton('Show it again'); rm.setToolTip('Stop leaving the selected note off')
            rm.clicked.connect(self._show_again)
            v.addWidget(rm, 0, Qt.AlignLeft)
        if changed:
            self.scale.currentIndexChanged.connect(lambda _: self.apply())
            self.out_group.idClicked.connect(lambda _: self.apply()); self.det.toggled.connect(lambda _: self.apply()); self.fan.toggled.connect(lambda _: self.apply()); self.opt.toggled.connect(lambda _: self.apply())

    def _show_again(self):
        for i in self.drop_list.selectedItems(): self.drop_list.takeItem(self.drop_list.row(i))
        if self.changed: self.apply()

    def apply(self):
        self.profile.inches_per_pt = self.scale.currentData() / 72
        self.profile.outlets = OUTLET_CHOICES[max(self.out_group.checkedId(), 0)][0]
        self.profile.detectors = self.det.isChecked(); self.profile.exhaust_fans = self.fan.isChecked()
        self.profile.optional_items = self.opt.isChecked()
        if self.drop_list is not None:
            keep = {self.drop_list.item(i).data(Qt.UserRole) for i in range(self.drop_list.count())}
            self.profile.drop_labels = [p for p in self.profile.drop_labels if p not in self.learned or p in keep]
        if self.changed: self.changed()


class BuilderSettingsDialog(QDialog):
    """What LET's finished plan shows for one builder (a new builder, or from the Teach panel)."""
    def __init__(self, profile, parent=None):
        super().__init__(parent); self.profile = profile
        self.setWindowTitle(f'Builder settings: {profile.name}')
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 16)
        t = QLabel(f'What to show on {profile.name} plans'); t.setStyleSheet('font-size:18px;font-weight:700'); v.addWidget(t)
        self.opts = BuilderOptions(profile, self); v.addWidget(self.opts)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); v.addSpacing(8); v.addWidget(bb)

    def accept(self):
        self.opts.apply(); super().accept()

def edit_builder_settings(settings, name, parent=None):
    """Open the settings for a saved builder and save them. True if saved."""
    path = settings.profile_path(name)
    p = Profile.load(path)
    if BuilderSettingsDialog(p, parent).exec() != QDialog.Accepted: return False
    p.save(path); return True

class BuildersPage(QWidget):
    """Every builder in one place: pick one on the left, act on it on the right."""
    def __init__(self, settings, parent=None, on_change=None):
        super().__init__(parent); self.settings = settings; self.on_change = on_change or (lambda *a: None)
        h = QHBoxLayout(self); h.setContentsMargins(0, 8, 0, 0); h.setSpacing(18)
        left = QVBoxLayout(); h.addLayout(left, 1)
        self.show_arch = QCheckBox('Show archived builders'); self.show_arch.toggled.connect(lambda _: self.refresh())
        self.lst = QListWidget(); self.lst.currentRowChanged.connect(lambda _: self.update_side())
        left.addWidget(self.lst, 1); left.addWidget(self.show_arch)
        nb = QPushButton('+ New builder…'); nb.clicked.connect(self.new); left.addWidget(nb)
        right = QVBoxLayout(); h.addLayout(right, 1)
        self.title = QLabel(''); self.title.setStyleSheet('font-size:18px;font-weight:700'); right.addWidget(self.title)
        self.desc = QLabel(''); self.desc.setWordWrap(True); self.desc.setProperty('kind', 'muted'); right.addWidget(self.desc)
        self.opts_box = QVBoxLayout(); right.addLayout(self.opts_box); self.opts = None
        self.saved_note = QLabel(''); self.saved_note.setProperty('kind', 'muted'); right.addWidget(self.saved_note)
        self.b_edit = QPushButton(); self.b_edit.hide()
        self.b_ren = QPushButton('Rename…'); self.b_ren.clicked.connect(self.rename)
        self.b_arch = QPushButton('Archive'); self.b_arch.setToolTip('Hide from the builder list. Nothing is lost; restore it any time.')
        self.b_arch.clicked.connect(self.archive)
        self.b_del = QPushButton('Delete…'); self.b_del.clicked.connect(self.delete)
        row = QHBoxLayout()
        for b in (self.b_ren, self.b_arch, self.b_del): row.addWidget(b)
        row.addStretch(); right.addLayout(row)
        right.addStretch(); self.refresh()

    def archive_dir(self):
        d = self.settings.profiles_dir() / 'Archived'; d.mkdir(exist_ok=True); return d
    def folder(self):
        return self.archive_dir() if self.show_arch.isChecked() else self.settings.profiles_dir()
    def name(self):
        it = self.lst.currentItem(); return it.text() if it else None

    def refresh(self, select=None):
        self.lst.clear()
        names = sorted(f.stem for f in self.folder().glob('*.json'))
        for n in names: self.lst.addItem(n)
        if names: self.lst.setCurrentRow(names.index(select) if select in names else 0)
        self.update_side()

    def update_side(self):
        n = self.name(); arch = self.show_arch.isChecked()
        for b in (self.b_edit, self.b_ren, self.b_arch, self.b_del): b.setEnabled(bool(n))
        self.b_ren.setVisible(not arch)
        if self.opts is not None: self.opts.setParent(None); self.opts.deleteLater(); self.opts = None
        self.saved_note.setText('')
        self.b_arch.setText('Restore' if arch else 'Archive')
        if not n:
            self.title.setText('No archived builders' if arch else 'No builders yet')
            self.desc.setText('' if arch else 'Click + New builder to add one.'); return
        self.title.setText(n)
        if arch: self.desc.setText('Archived: hidden from the builder list. Restore it to use it again.'); return
        try:
            p = Profile.load(self.folder() / (n + '.json'))
            self.desc.setText(f'{len(p.symbols)} symbols taught. What shows on their plans:')
            path = self.folder() / (n + '.json')
            def save(p=p, path=path, n=n):
                p.save(path); self.saved_note.setText('✓ Saved'); self.on_change(n)
            self.opts = BuilderOptions(p, self, changed=save); self.opts_box.addWidget(self.opts)
        except Exception as e: self.desc.setText(f'Could not read this builder ({e}).')

    def new(self):
        name, ok = QInputDialog.getText(self, 'New builder', 'Builder name:')
        if not (ok and name.strip()): return
        if self.settings.profile_path(name).exists(): QMessageBox.warning(self, 'New builder', 'There is already a builder with that name.'); return
        p = Profile(name=name.strip())
        if BuilderSettingsDialog(p, self).exec() != QDialog.Accepted: return
        p.save(self.settings.profile_path(p.name))
        self.show_arch.setChecked(False); self.refresh(safe_name(p.name)); self.on_change()

    def edit(self):
        if self.name(): edit_builder_settings(self.settings, self.name(), self); self.update_side(); self.on_change(self.name())

    def rename(self):
        old = self.name()
        if not old: return
        new, ok = QInputDialog.getText(self, 'Rename builder', 'New name:', text=old)
        if not (ok and new.strip()) or safe_name(new) == old: return
        dst = self.settings.profile_path(new)
        if dst.exists(): QMessageBox.warning(self, 'Rename builder', 'There is already a builder with that name.'); return
        src = self.settings.profile_path(old); p = Profile.load(src); p.name = new.strip(); p.save(dst); src.unlink()
        self.refresh(safe_name(new)); self.on_change(safe_name(new), old)

    def archive(self):
        n = self.name()
        if not n: return
        src, dst = (self.archive_dir(), self.settings.profiles_dir()) if self.show_arch.isChecked() else (self.settings.profiles_dir(), self.archive_dir())
        if (dst / (n + '.json')).exists(): QMessageBox.warning(self, 'Builders', f'There is already a builder called "{n}" there.'); return
        (src / (n + '.json')).replace(dst / (n + '.json')); self.refresh(); self.on_change(None, n)

    def delete(self):
        n = self.name()
        if not n: return
        box = QMessageBox(QMessageBox.Warning, 'Delete builder', f'Delete "{n}" and everything taught for it? This can\'t be undone.\n\n'
                          'To keep a builder but hide it from the list, use Archive instead.', QMessageBox.Cancel, self)
        yes = box.addButton('Delete', QMessageBox.DestructiveRole); box.setDefaultButton(QMessageBox.Cancel); box.exec()
        if box.clickedButton() is not yes: return
        try: (self.folder() / (n + '.json')).unlink()
        except OSError: pass
        self.refresh(); self.on_change(None, n)


class ManageBuildersDialog(QDialog):
    """The Builders page on its own (from the New plan window)."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.setWindowTitle('Builders'); self.resize(720, 460)
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 16)
        self.page = BuildersPage(settings, self); v.addWidget(self.page, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(self.reject); v.addWidget(bb)


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
        row = QHBoxLayout()
        t = QLabel('New plan'); t.setStyleSheet('font-size:26px;font-weight:700'); row.addWidget(t); row.addStretch()
        op = _big('Open a saved plan…'); op.setToolTip('Pick up a plan you started before'); op.clicked.connect(self.open_saved); row.addWidget(op)
        v.addLayout(row)
        from planclean.plan import Plan
        recent = Plan.list_saved()[:4]
        if recent:
            row = QHBoxLayout(); lab = QLabel('Continue a recent plan:'); lab.setProperty('kind', 'muted'); row.addWidget(lab)
            for name, folder, _, builder in recent:
                # the plan's name, and the builder under it in small grey text
                b = QPushButton(); b.setCursor(Qt.PointingHandCursor); b.setToolTip('Open this saved plan')
                bl = QVBoxLayout(b); bl.setContentsMargins(12, 5, 12, 5); bl.setSpacing(0)
                n = QLabel(name); n.setStyleSheet('font-weight:600;background:transparent'); bl.addWidget(n)
                if builder:
                    sb = QLabel(builder); sb.setStyleSheet('font-size:11px;color:#7a8794;background:transparent'); bl.addWidget(sb)
                for w in (n, *( [sb] if builder else [])): w.setAttribute(Qt.WA_TransparentForMouseEvents)
                b.setMinimumSize(bl.sizeHint()); b.setMinimumHeight(bl.sizeHint().height() + 4)
                b.clicked.connect(lambda *_, f=folder: self._open_recent(f)); row.addWidget(b)
            row.addStretch(); v.addLayout(row)
        sub = QLabel('Name it, add the builder\'s files, and choose the builder. Everything you clean goes into one drawing, '
                     'and the plan is saved so you can come back to it.'); sub.setProperty('kind', 'muted'); sub.setWordWrap(True); v.addWidget(sub)
        v.addWidget(_heading('1', 'Plan name'))
        self.plan_name = QLineEdit(); self.plan_name.setPlaceholderText('e.g. C405 Electrical'); self.plan_name.setMinimumHeight(34)
        self.plan_name.textEdited.connect(lambda _: setattr(self, '_named', True)); self.plan_name.textChanged.connect(self.update_state)
        v.addWidget(self.plan_name)
        row = QHBoxLayout()
        self.project = QLineEdit(); self.project.setPlaceholderText('Community'); self.project.setMinimumHeight(30)
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
        row.addStretch()
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

    def _open_recent(self, folder):
        self.open_folder = folder; self.accept()

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
