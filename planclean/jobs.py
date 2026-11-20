"""Run a PDF cleanup in a separate process.

The cleanup is heavy (OCR, template matching, tens of thousands of paths). Running it in
its own process keeps the window responsive on every OS, avoids sharing a PyMuPDF
document between threads, and lets the user cancel it.
"""
from __future__ import annotations
import multiprocessing as mp
import queue, time, traceback


def _clean_pdf_worker(pdf_path, page_index, region, profile, q):
    try:
        import pymupdf
        from planclean.engine import clean_pdf
        page = pymupdf.open(pdf_path)[page_index]
        res = clean_pdf(page, region, profile, progress=lambda m: q.put(('progress', m)))
        res.to_page = None                      # functions can't cross processes
        q.put(('done', res))
    except Exception:
        q.put(('failed', traceback.format_exc()))


def attach_to_page(res, profile, region):
    K, x0, y0 = profile.inches_per_pt, region[0], region[1]
    res.to_page = lambda X, Y: (X / K + x0, -Y / K + y0)
    return res


class CleanJob:
    """Start with start(); call poll() regularly. poll() returns a list of (kind, payload)."""
    def __init__(self, pdf_path, page_index, region, profile):
        self.args = (pdf_path, page_index, tuple(region), profile)
        self.ctx = mp.get_context('spawn')
        self.q = self.ctx.Queue()
        self.proc = None
        self.t0 = None
        self.finished = False

    def start(self):
        self.proc = self.ctx.Process(target=_clean_pdf_worker, args=(*self.args, self.q), daemon=True)
        self.proc.start(); self.t0 = time.time()

    def elapsed(self):
        return time.time() - self.t0 if self.t0 else 0.0

    def poll(self):
        out = []
        while True:
            try: msg = self.q.get_nowait()
            except queue.Empty: break
            out.append(msg)
            if msg[0] in ('done', 'failed'): self.finished = True
        if not self.finished and self.proc is not None and not self.proc.is_alive():
            # the worker died without reporting (crash, out of memory)
            try: msg = self.q.get(timeout=0.5); out.append(msg); self.finished = msg[0] in ('done', 'failed')
            except queue.Empty: pass
            if not self.finished:
                self.finished = True
                out.append(('failed', f'The cleanup stopped unexpectedly (exit code {self.proc.exitcode}).'))
        return out

    def cancel(self):
        if self.proc is not None and self.proc.is_alive():
            self.proc.terminate(); self.proc.join(2)
        self.finished = True


def _ping(q):
    q.put('pong')


def selftest_process():
    """True if a cleanup process can start (checks the packaged app's multiprocessing setup)."""
    ctx = mp.get_context('spawn'); q = ctx.Queue()
    p = ctx.Process(target=_ping, args=(q,)); p.start()
    try: ok = q.get(timeout=60) == 'pong'
    except queue.Empty: ok = False
    p.join(10)
    return ok
