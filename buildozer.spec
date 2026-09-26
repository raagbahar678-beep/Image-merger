[app]

# (str) Title of your application
title = Line Replacement Tool

# (str) Package name
package.name = linereplacementtool

# (str) Package domain (needed for android/ios packaging)
package.domain = org.example

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,txt

# (str) Application versioning
version = 1.0

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
# pyjnius is required for the native Android file picker (jnius/android
# imports in main.py) - without it the app crashes on launch on-device.
# python3 is pinned to 3.11 because unpinned it resolves to whatever
# p4a's python3 recipe currently defaults to (3.14.2 as of the p4a
# 2026.5.9 release) and that hostpython's bundled pip breaks on its own
# self-upgrade during the dependency-install stage:
#   ImportError: cannot import name 'BuildDependencyInstallError'
#   from 'pip._internal.exceptions'
# 3.11 is a version p4a/kivy have long been built and tested against.
requirements = python3==3.11.9,kivy,pyjnius

# (str) Presplash of the application
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
#icon.filename = %(source.dir)s/data/icon.png

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (list) Permissions
# ACTION_OPEN_DOCUMENT (used for file picking) grants scoped, temporary
# access to whatever the user selects, so broad storage permissions
# aren't needed. MANAGE_EXTERNAL_STORAGE in particular requires special
# Play Store review and a manual settings toggle on API 30+ - dropped.
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# (int) Target Android API, should be as high as possible.
android.api = 33

# (bool) Automatically accept Android SDK license agreements. Needed for
# unattended CI builds, since the license prompt otherwise has no input to
# read and buildozer treats that as a decline, leaving newly-required SDK
# packages (e.g. a newer Build-Tools version this project isn't pinned to)
# uninstalled.
android.accept_sdk_license = True

# (int) Minimum API your APK / AAB will support.
android.minapi = 21

# (str) Android NDK version to use
android.ndk = 25b

# (str) python-for-android branch to use.
# python-for-android's own contributing guide states "master" always
# represents the latest stable release (currently the 2026.5.9 release
# that's live on PyPI) - there is no separate git tag matching the PyPI
# version string, which is why pinning to "2026.5.9" failed to clone.
# "master" gives that same stable code without develop's active churn
# (which broke the build with an unrelated libthorvg recipe bug).
p4a.branch = master

# (bool) Use --private data storage (True) or --dir public storage (False)
#android.private_storage = True

# (str) Android entry point, default is ok for Kivy-based app
#android.entrypoint = org.kivy.android.PythonActivity

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
android.archs = arm64-v8a,armeabi-v7a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

# (int) Android logcat filters to use
android.logcat_filters = *:S python:D

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
