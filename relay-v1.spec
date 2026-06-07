# relay-v1.spec
# Jalankan dengan: C:\Python64\python.exe -m PyInstaller relay-v1.spec

block_cipher = None

a = Analysis(
    ['relay-v1.py'],
    pathex=[],
    binaries=[
        ('usb_relay_device.dll', '.'),
    ],
    datas=[],
    hiddenimports=[
        'pynput',
        'pynput.mouse',
        'pynput.keyboard',
        'pynput.mouse._win32',
        'pynput.keyboard._win32',
        'pynput._util',
        'pynput._util.win32',
        'cv2',
        'numpy',
        'pyautogui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pystray',
        'pystray._win32',
        'ttkbootstrap',
        'ttkbootstrap.themes',
        'ttkbootstrap.style',
        'sqlite3',
        'winreg',
        'ctypes',
        'ctypes.wintypes',
        'threading',
        'signal',
        'atexit',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='relay-v1',
    debug=False,
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
    onefile=True,
)
