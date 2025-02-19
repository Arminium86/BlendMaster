# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\BlendMaster\\blendmaster_OOP\\GUI\\InitialiseGUI.py'],
    pathex=['C:\\BlendMaster\\blendmaster_OOP'],
    binaries=[],
    datas=[('C:\\BlendMaster\\blendmaster_OOP\\requirements.txt', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\blendmaster.db', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\background.png', 'resources')],
    hiddenimports=['all'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6'],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('v', None, 'OPTION')],
    name='BlendMaster PoC v0.1.0',
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\BlendMaster\\blendmaster_OOP\\resources\\icon_2.ico'],
)
