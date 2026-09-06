# Ricetta PyInstaller di Scriba.
# Autori: Gabriele Battaglia (IZ4APU) & ClaudIA (Claude Opus 5, modalita' auto).
# Il manuale viaggia dentro l'eseguibile: la voce guida del menu lo cerca in
# sys._MEIPASS, dove PyInstaller estrae quel che sta in datas.
# sounddevice porta con se' la libreria PortAudio, che va dichiarata a mano
# perche' non e' un import ma una DLL caricata a runtime.

a = Analysis(
    ['scriba.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('Manuale_Scriba.txt', '.'),
    ],
    hiddenimports=[
        'sounddevice',
        'numpy',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Arrivano di rimbalzo dall'analisi di GBUtils, che e' un modulo solo
        # e contiene anche cio' che serve ai progetti grafici. Scriba non ne
        # usa nemmeno uno: senza, il pacchetto passa da 293 MB a molto meno.
        'PyQt6',
        'PyQt5',
        'PySide6',
        'wx',
        'matplotlib',
        'pandas',
        'PIL',
        'jedi',
        'IPython',
        'pytest',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='scriba',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='scriba',
)
