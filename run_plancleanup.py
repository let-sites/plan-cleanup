import sys

def selftest(out_path):
    """Used by the build to check the packaged app has everything it needs."""
    import numpy as np, cv2, pymupdf, ezdxf
    from planclean.ocr import engine
    from planclean import engine as _e, dwgin, dxfout, oda  # noqa: F401
    img = np.full((80, 300, 3), 255, np.uint8)
    cv2.putText(img, 'KITCHEN', (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    res, _ = engine()(img)
    text = ' '.join(r[1] for r in (res or []))
    with open(out_path, 'w') as f:
        f.write(text)
    from planclean.jobs import selftest_process
    proc_ok = selftest_process()
    with open(out_path, 'a') as f:
        f.write('\nprocess ok' if proc_ok else '\nprocess FAILED')
    return 0 if 'KITCHEN' in text.upper() and proc_ok else 1

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()   # the packaged app re-runs itself for the cleanup process
    if len(sys.argv) > 2 and sys.argv[1] == '--selftest':
        sys.exit(selftest(sys.argv[2]))
    from planclean.gui.main import main
    main()
