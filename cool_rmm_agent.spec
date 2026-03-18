# cool_rmm_agent.spec
# ─────────────────────────────────────────────────────────────────────────────
# PyInstaller spec for Cool RMM Agent
# Produces a single, standalone .exe with no Python dependency on target
#
# Usage:
#   pip install pyinstaller
#   pyinstaller cool_rmm_agent.spec
#
# Output: dist\cool_rmm_agent.exe
# ─────────────────────────────────────────────────────────────────────────────

block_cipher = None

a = Analysis(
    ['cool_rmm_agent.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'psutil',
        'requests',
        'winreg',
        'subprocess',
        'socket',
        'platform',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'PIL',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cool_rmm_agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress the exe — requires UPX installed
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no console window — runs silently as a service
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Optional: give it a version and icon
    # version='version_info.txt',
    # icon='coolrmm.ico',
)
