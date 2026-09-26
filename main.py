import os
import threading
import traceback
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.utils import platform
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup

# ---------------------------------------------------------------------------
# Android native document picker.
#
# FileChooserListView is a pure-Kivy widget that just lists whatever
# directory you point it at on the raw filesystem. On modern Android it
# often opens onto a path the app can't actually see anything in (scoped
# storage), which is why it looked empty. The real fix is to launch
# Android's own document picker (Intent.ACTION_OPEN_DOCUMENT) - the same
# UI every app (WhatsApp, Gmail, etc.) uses - via pyjnius, and then stream
# whatever the user picked into a local file we can open by path.
# ---------------------------------------------------------------------------

ANDROID = platform == "android"

if ANDROID:
    from jnius import autoclass, cast
    from android import activity, mActivity

    Intent = autoclass("android.content.Intent")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")


class AndroidFilePicker:
    """Launches the system document picker and streams the chosen content
    into app-private storage, in chunks, so multi-GB files never get
    fully loaded into memory just to be "picked"."""

    _request_code_counter = 9100

    def __init__(self):
        self._callback = None
        self._request_code = None
        if ANDROID:
            activity.bind(on_activity_result=self._on_activity_result)

    def pick(self, on_picked, on_error=None):
        """on_picked(local_path) is called on success (main thread).
        on_error(message) is called on failure/cancel."""
        self._callback = on_picked
        self._error_callback = on_error
        AndroidFilePicker._request_code_counter += 1
        self._request_code = AndroidFilePicker._request_code_counter

        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")
        mActivity.startActivityForResult(intent, self._request_code)

    def _on_activity_result(self, request_code, result_code, data):
        if request_code != self._request_code:
            return
        if data is None:
            if self._error_callback:
                self._error_callback("No file selected")
            return

        uri = data.getData()
        threading.Thread(
            target=self._copy_uri_to_local, args=(uri,), daemon=True
        ).start()

    def _copy_uri_to_local(self, uri):
        try:
            resolver = mActivity.getContentResolver()

            # Try to recover the original display name so the local copy
            # keeps a sensible extension/name.
            display_name = "picked_file"
            try:
                DocumentsContract = autoclass("android.provider.DocumentsContract")
                cursor = resolver.query(uri, None, None, None, None)
                if cursor and cursor.moveToFirst():
                    name_idx = cursor.getColumnIndex("_display_name")
                    if name_idx != -1:
                        display_name = cursor.getString(name_idx)
                    cursor.close()
            except Exception:
                pass

            cache_dir = mActivity.getCacheDir().getAbsolutePath()
            local_path = os.path.join(cache_dir, display_name)

            input_stream = resolver.openInputStream(uri)
            CHUNK = 1024 * 1024  # 1 MB at a time - smooth for MB-to-GB files
            # '[B' is the JNI type signature for a Java byte[] - this is
            # the correct pyjnius way to allocate one. The previous
            # java.lang.reflect.Array.newInstance(autoclass("byte"), ...)
            # tried to autoclass a primitive type name ("byte"), which
            # Java's class-loading has no class for, and raised on every
            # single pick - silently, since the failure was only ever
            # surfaced as a generic error the UI didn't display (see the
            # _on_android_error fix below).
            ByteArray = autoclass('[B')
            with open(local_path, "wb") as out_f:
                Buffer = ByteArray(CHUNK)
                while True:
                    n = input_stream.read(Buffer)
                    if n == -1:
                        break
                    out_f.write(bytes(Buffer[:n]))
            input_stream.close()

            Clock.schedule_once(lambda _dt: self._callback(local_path), 0)
        except Exception as e:
            err = f"Failed to read picked file: {e}"
            if self._error_callback:
                Clock.schedule_once(lambda _dt: self._error_callback(err), 0)


class ReplacementEngine:
    """Holds the original line-replacement logic, unchanged in behavior,
    just adapted to run as a method that reports progress via self.log()
    instead of print(), and takes file paths as arguments instead of
    hardcoded constants."""

    def __init__(self, log_callback):
        self.log = log_callback

    def run(self, file1_path, file2_path, map_path, output_dir):
        # File paths
        output_path = os.path.join(output_dir, "result.txt")
        log_path = os.path.join(output_dir, "replacement_log.txt")

        # Make sure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        self.log("="*60)
        self.log("LINE REPLACEMENT TOOL")
        self.log("="*60)
        
        # Read all lines from file1 and file2 into lists (line numbers start at 1)
        self.log("\n[1/5] Reading input files...")
        with open(file1_path, "r", encoding="utf-8") as f:
            file1_lines = f.readlines()
        self.log(f"  ✓ File1: {len(file1_lines)} lines loaded")
        
        with open(file2_path, "r", encoding="utf-8") as f:
            file2_lines = f.readlines()
        self.log(f"  ✓ File2: {len(file2_lines)} lines loaded")
        
        # Read and parse the map file
        self.log("\n[2/5] Parsing and validating map file...")
        replacements = []
        log_entries = []
        validation_results = []
        
        with open(map_path, "r", encoding="utf-8") as f:
            map_lines = f.readlines()
        
        total_mappings = len([l for l in map_lines if l.strip() and "=" in l])
        self.log(f"  ✓ Found {total_mappings} mapping(s) to process")
        
        mapping_num = 0
        for line_num, line in enumerate(map_lines, 1):
            line = line.strip()
            if not line or "=" not in line:
                continue
            
            mapping_num += 1
            errors = []
            warnings = []
            
            try:
                left, right = line.split("=")
                
                # Parse left side (file1 lines)
                if "," in left:
                    left_parts = left.split(",")
                    start1 = int(left_parts[0])
                    end1 = int(left_parts[1])
                else:
                    start1 = end1 = int(left)
                
                # Validate file1 range
                if start1 < 1 or end1 > len(file1_lines):
                    errors.append(f"File1 range {start1}-{end1} is out of bounds (1-{len(file1_lines)})")
                if start1 > end1:
                    errors.append(f"File1 range {start1}-{end1} is invalid (start > end)")
                
                # Parse right side (file2 lines) - supports multiple ranges
                right_parts = [int(x.strip()) for x in right.split(",")]
                
                # Collect all file2 line ranges
                file2_ranges = []
                i = 0
                while i < len(right_parts):
                    if i + 1 < len(right_parts):
                        start2 = right_parts[i]
                        end2 = right_parts[i + 1]
                        file2_ranges.append((start2, end2))
                        i += 2
                    else:
                        # Single line at the end
                        single = right_parts[i]
                        file2_ranges.append((single, single))
                        i += 1
                
                # Validate file2 ranges
                for idx, (start2, end2) in enumerate(file2_ranges):
                    if start2 < 1 or end2 > len(file2_lines):
                        errors.append(f"File2 range #{idx+1} ({start2}-{end2}) is out of bounds (1-{len(file2_lines)})")
                    if start2 > end2:
                        errors.append(f"File2 range #{idx+1} ({start2}-{end2}) is invalid (start > end)")
                
                # Check for overlapping ranges in file1
                for prev_start, prev_end, _ in replacements:
                    if not (end1 < prev_start or start1 > prev_end):
                        warnings.append(f"Overlaps with previous mapping at file1 lines {prev_start}-{prev_end}")
                
                status = "✓ SUCCESS" if not errors else "✗ FAILED"
                
                replacements.append((start1, end1, file2_ranges))
                
                # Create log entry
                file1_str = f"{start1}-{end1}" if start1 != end1 else str(start1)
                ranges_str = ", ".join([f"{s}-{e}" if s != e else str(s) for s, e in file2_ranges])
                
                validation_results.append({
                    'num': mapping_num,
                    'line_num': line_num,
                    'file1_range': file1_str,
                    'file2_ranges': ranges_str,
                    'status': status,
                    'errors': errors,
                    'warnings': warnings
                })
                
                log_entries.append(f"Map #{mapping_num}: File1 lines {file1_str} → File2 lines [{ranges_str}]")
                
            except Exception as e:
                status = "✗ FAILED"
                errors.append(f"Parse error: {str(e)}")
                validation_results.append({
                    'num': mapping_num,
                    'line_num': line_num,
                    'file1_range': 'N/A',
                    'file2_ranges': 'N/A',
                    'status': status,
                    'errors': errors,
                    'warnings': []
                })
        
        # Display validation results
        self.log("\n[3/5] Validation Results:")
        self.log("-"*60)
        success_count = 0
        failed_count = 0
        for result in validation_results:
            self.log(f"  Map #{result['num']} (line {result['line_num']}): {result['status']}")
            self.log(f"    File1: {result['file1_range']} → File2: [{result['file2_ranges']}]")
            
            if result['errors']:
                failed_count += 1
                for err in result['errors']:
                    self.log(f"    ✗ ERROR: {err}")
            else:
                success_count += 1
            
            if result['warnings']:
                for warn in result['warnings']:
                    self.log(f"    ⚠ WARNING: {warn}")
            self.log("")
        
        self.log(f"  Validation Summary: {success_count} succeeded, {failed_count} failed")
        
        # Only proceed if all validations passed
        if failed_count > 0:
            self.log("\n✗ Cannot proceed due to validation errors. Please fix the map file.")
            self.log(f"  Check log file for details: {log_path}")
            # Still write log file
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("="*60 + "\n")
                f.write("LINE REPLACEMENT LOG - VALIDATION FAILED\n")
                f.write("="*60 + "\n")
                f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                for result in validation_results:
                    f.write(f"Map #{result['num']} (line {result['line_num']}): {result['status']}\n")
                    f.write(f"  File1: {result['file1_range']} → File2: [{result['file2_ranges']}]\n")
                    for err in result['errors']:
                        f.write(f"  ✗ ERROR: {err}\n")
                    for warn in result['warnings']:
                        f.write(f"  ⚠ WARNING: {warn}\n")
                    f.write("\n")
            return
        
        # Sort replacements by start1 to process in order
        replacements.sort(key=lambda x: x[0])
        
        # Build output lines with progress
        self.log("\n[4/5] Processing replacements...")
        output_lines = []
        current_line = 1
        rep_index = 0
        num_file1_lines = len(file1_lines)
        processed_count = 0
        total_lines_inserted = 0
        processing_results = []
        
        while current_line <= num_file1_lines:
            # Check if current_line is start of any replacement range
            if rep_index < len(replacements) and current_line == replacements[rep_index][0]:
                start1, end1, file2_ranges = replacements[rep_index]
                
                lines_inserted = 0
                # Append lines from all file2 ranges
                for start2, end2 in file2_ranges:
                    chunk = file2_lines[start2-1:end2]
                    output_lines.extend(chunk)
                    lines_inserted += len(chunk)
                
                total_lines_inserted += lines_inserted
                processed_count += 1
                
                file1_str = f"{start1}-{end1}" if start1 != end1 else str(start1)
                
                # Progress feedback
                self.log(f"  [{processed_count}/{len(replacements)}] ✓ Replaced file1 lines {file1_str} with {lines_inserted} lines from file2")
                
                processing_results.append({
                    'num': processed_count,
                    'file1_range': file1_str,
                    'lines_inserted': lines_inserted,
                    'status': '✓ SUCCESS'
                })
                
                # Skip lines in file1 replaced by this block
                current_line = end1 + 1
                rep_index += 1
            else:
                # Just copy line from file1
                output_lines.append(file1_lines[current_line - 1])
                current_line += 1
        
        self.log(f"  ✓ All {processed_count} replacement(s) completed successfully")
        
        # Write result to output file
        self.log("\n[5/5] Writing output file...")
        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(output_lines)
        self.log(f"  ✓ Output saved to: {output_path}")
        self.log(f"  ✓ Total lines in output: {len(output_lines)}")
        
        # Write detailed log
        self.log("\n[6/6] Generating log file...")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("="*60 + "\n")
            f.write("LINE REPLACEMENT LOG - SUCCESS\n")
            f.write("="*60 + "\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("INPUT FILES:\n")
            f.write(f"  File1: {file1_path} ({len(file1_lines)} lines)\n")
            f.write(f"  File2: {file2_path} ({len(file2_lines)} lines)\n")
            f.write(f"  Map:   {map_path} ({total_mappings} mappings)\n\n")
            
            f.write("OUTPUT:\n")
            f.write(f"  Result: {output_path} ({len(output_lines)} lines)\n\n")
            
            f.write("VALIDATION RESULTS:\n")
            f.write("-"*60 + "\n")
            for result in validation_results:
                f.write(f"Map #{result['num']} (line {result['line_num']}): {result['status']}\n")
                f.write(f"  File1: {result['file1_range']} → File2: [{result['file2_ranges']}]\n")
                for err in result['errors']:
                    f.write(f"  ✗ ERROR: {err}\n")
                for warn in result['warnings']:
                    f.write(f"  ⚠ WARNING: {warn}\n")
                f.write("\n")
            f.write(f"Summary: {success_count} succeeded, {failed_count} failed\n")
            f.write("-"*60 + "\n\n")
            
            f.write("PROCESSING RESULTS:\n")
            f.write("-"*60 + "\n")
            for result in processing_results:
                f.write(f"Map #{result['num']}: {result['status']}\n")
                f.write(f"  File1 range: {result['file1_range']}\n")
                f.write(f"  Lines inserted: {result['lines_inserted']}\n\n")
            f.write("-"*60 + "\n\n")
            
            f.write("REPLACEMENT SUMMARY:\n")
            f.write(f"  Total mappings processed: {processed_count}\n")
            f.write(f"  Total lines inserted from file2: {total_lines_inserted}\n\n")
            
            f.write("DETAILED MAPPINGS:\n")
            f.write("-"*60 + "\n")
            for entry in log_entries:
                f.write(entry + "\n")
            f.write("-"*60 + "\n")
        
        self.log(f"  ✓ Log saved to: {log_path}")
        
        # Final summary
        self.log("\n" + "="*60)
        self.log("SUMMARY")
        self.log("="*60)
        self.log(f"✓ Validated {success_count}/{total_mappings} mappings successfully")
        self.log(f"✓ Processed {processed_count}/{total_mappings} mappings")
        self.log(f"✓ Inserted {total_lines_inserted} lines from file2")
        self.log(f"✓ Output file: {len(output_lines)} total lines")
        self.log(f"✓ Log file: {log_path}")
        self.log("="*60)
        self.log("\n✓ Replacement complete!")
        return output_path, log_path

# ---------------------------------------------------------------------------
# Kivy UI wrapping the ReplacementEngine above.
# Lets the user pick File1, File2 and the Map file with the Android file
# chooser, then runs the same logic and shows the log output on screen.
# ---------------------------------------------------------------------------

class PathPickerRow(BoxLayout):
    """A label + 'Pick file' button used to pick one file.

    No manual text entry. On Android this opens the real system file
    manager / document picker (the same one every app uses); elsewhere
    (desktop testing) it falls back to Kivy's own file chooser.
    """

    def __init__(self, title, default_path=None, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=70, **kwargs)
        self.title = title
        self.selected_path = default_path

        top = BoxLayout(size_hint_y=None, height=44, spacing=8)
        top.add_widget(Label(text=title, size_hint_x=0.25))
        self.pick_btn = Button(text="Pick file", size_hint_x=0.25)
        self.pick_btn.bind(on_release=self.open_picker)
        top.add_widget(self.pick_btn)
        self.path_label = Label(
            text=self.selected_path or "No file selected",
            size_hint_x=0.5,
            shorten=True,
            shorten_from="left",
        )
        top.add_widget(self.path_label)
        self.add_widget(top)

        self._android_picker = AndroidFilePicker() if ANDROID else None

    def open_picker(self, *_args):
        if ANDROID:
            self.pick_btn.disabled = True
            self.path_label.text = "Opening file manager..."
            self._android_picker.pick(
                on_picked=self._on_android_picked,
                on_error=self._on_android_error,
            )
        else:
            self._open_kivy_chooser()

    def _on_android_picked(self, local_path):
        self.pick_btn.disabled = False
        self.selected_path = local_path
        self.path_label.text = local_path

    def _on_android_error(self, message):
        self.pick_btn.disabled = False
        self.path_label.text = f"Error: {message}" if message else (
            self.selected_path or "No file selected"
        )

    def _open_kivy_chooser(self):
        content = BoxLayout(orientation="vertical")
        start_path = os.path.dirname(self.selected_path) if self.selected_path else os.path.expanduser("~")
        chooser = FileChooserListView(path=start_path)
        content.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height=44)
        popup = Popup(title=f"Select {self.title}", content=content, size_hint=(0.9, 0.9))

        def select(*_a):
            if chooser.selection:
                self.selected_path = chooser.selection[0]
                self.path_label.text = self.selected_path
            popup.dismiss()

        select_btn = Button(text="Select")
        select_btn.bind(on_release=select)
        cancel_btn = Button(text="Cancel")
        cancel_btn.bind(on_release=popup.dismiss)
        buttons.add_widget(select_btn)
        buttons.add_widget(cancel_btn)
        content.add_widget(buttons)
        popup.open()

    @property
    def value(self):
        return (self.selected_path or "").strip()


class OutputDirPickerRow(BoxLayout):
    """Directory picker (Android has no ACTION_OPEN_DOCUMENT equivalent as
    simple as the file picker for choosing a plain writable folder, so this
    uses Kivy's chooser pointed at the app's own storage area, with
    dirselect enabled). No manual typing."""

    def __init__(self, title, default_path, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=70, **kwargs)
        self.title = title
        # Create the default output dir immediately rather than waiting
        # for a run - otherwise os.path.isdir(self.selected_path) below is
        # False the first time the picker opens, and it falls back to
        # os.path.expanduser("~"), which on Android is usually a path the
        # app can't list at all. FileChooserListView tries to list its
        # start path as soon as it's constructed, so that failure happened
        # before the popup ever opened - nothing visibly happened when
        # tapping "Pick folder".
        try:
            os.makedirs(default_path, exist_ok=True)
        except Exception:
            pass
        self.selected_path = default_path
        self._fallback_base = os.path.dirname(default_path) or default_path

        top = BoxLayout(size_hint_y=None, height=44, spacing=8)
        top.add_widget(Label(text=title, size_hint_x=0.25))
        pick_btn = Button(text="Pick folder", size_hint_x=0.25)
        pick_btn.bind(on_release=self.open_chooser)
        top.add_widget(pick_btn)
        self.path_label = Label(
            text=self.selected_path, size_hint_x=0.5, shorten=True, shorten_from="left"
        )
        top.add_widget(self.path_label)
        self.add_widget(top)

    def open_chooser(self, *_args):
        content = BoxLayout(orientation="vertical")
        start_path = (
            self.selected_path if os.path.isdir(self.selected_path) else self._fallback_base
        )
        try:
            chooser = FileChooserListView(path=start_path, dirselect=True)
        except Exception as e:
            # Listing start_path failed (e.g. no permission) - show that
            # instead of doing nothing, and fall back to a path the app
            # definitely owns.
            self.path_label.text = f"Error opening folder picker: {e}"
            try:
                chooser = FileChooserListView(path=self._fallback_base, dirselect=True)
            except Exception:
                return
        content.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height=44)
        popup = Popup(title=f"Select {self.title}", content=content, size_hint=(0.9, 0.9))

        def select(*_a):
            self.selected_path = chooser.selection[0] if chooser.selection else chooser.path
            self.path_label.text = self.selected_path
            popup.dismiss()

        select_btn = Button(text="Select")
        select_btn.bind(on_release=select)
        cancel_btn = Button(text="Cancel")
        cancel_btn.bind(on_release=popup.dismiss)
        buttons.add_widget(select_btn)
        buttons.add_widget(cancel_btn)
        content.add_widget(buttons)
        popup.open()

    @property
    def value(self):
        return (self.selected_path or "").strip()


class RootWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=10, spacing=8, **kwargs)

        if ANDROID:
            base = mActivity.getFilesDir().getAbsolutePath()
        else:
            base = os.path.join(os.path.expanduser("~"), "LineReplacementTool")
        self.default_output_dir = os.path.join(base, "output")

        self.file1_row = PathPickerRow("File 1")
        self.file2_row = PathPickerRow("File 2")
        self.map_row = PathPickerRow("Map file")
        self.output_row = OutputDirPickerRow("Output dir", self.default_output_dir)

        self.add_widget(self.file1_row)
        self.add_widget(self.file2_row)
        self.add_widget(self.map_row)
        self.add_widget(self.output_row)

        self.run_btn = Button(text="Run Replacement", size_hint_y=None, height=50)
        self.run_btn.bind(on_release=self.run_replacement)
        self.add_widget(self.run_btn)

        self.log_output = TextInput(
            text="", readonly=True, font_size="13sp", background_color=(0, 0, 0, 1),
            foreground_color=(0.2, 1, 0.2, 1)
        )
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self.log_output)
        self.add_widget(scroll)

        self.engine = ReplacementEngine(log_callback=self.append_log_threadsafe)

    def append_log_threadsafe(self, message=""):
        """Safe to call from the background worker thread; Kivy widgets
        must only be touched on the main thread, so we hop back via Clock."""
        Clock.schedule_once(lambda _dt: self._append_log(message), 0)

    def _append_log(self, message=""):
        self.log_output.text += str(message) + "\n"

    def run_replacement(self, *_args):
        file1 = self.file1_row.value
        file2 = self.file2_row.value
        map_file = self.map_row.value
        output_dir = self.output_row.value or self.default_output_dir

        if not file1 or not file2 or not map_file:
            self.log_output.text = ""
            self._append_log("✗ ERROR: please pick File 1, File 2 and the Map file first.")
            return

        self.log_output.text = ""
        self.run_btn.disabled = True
        self.run_btn.text = "Running..."

        def worker():
            try:
                self.engine.run(file1, file2, map_file, output_dir)
            except Exception:
                self.append_log_threadsafe(f"\n✗ ERROR: {traceback.format_exc()}")
            finally:
                Clock.schedule_once(lambda _dt: self._reset_run_button(), 0)

        # Background thread keeps the UI smooth even for multi-GB files.
        threading.Thread(target=worker, daemon=True).start()

    def _reset_run_button(self):
        self.run_btn.disabled = False
        self.run_btn.text = "Run Replacement"


class LineReplacementApp(App):
    title = "Line Replacement Tool"

    def build(self):
        return RootWidget()


if __name__ == "__main__":
    LineReplacementApp().run()
