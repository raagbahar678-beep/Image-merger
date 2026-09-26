import os
import threading
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup


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

    No manual text entry: the only way to set the path is through the
    native file chooser, which keeps things safe/consistent even when
    the picked file is very large (MBs to GBs) — we never read or copy
    the file just to display its path, we only store the path string."""

    def __init__(self, title, default_path=None, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=70, **kwargs)
        self.title = title
        self.selected_path = default_path  # None until the user picks a file

        top = BoxLayout(size_hint_y=None, height=44, spacing=8)
        top.add_widget(Label(text=title, size_hint_x=0.25))
        pick_btn = Button(text="Pick file", size_hint_x=0.25)
        pick_btn.bind(on_release=self.open_chooser)
        top.add_widget(pick_btn)
        self.path_label = Label(
            text=self.selected_path or "No file selected",
            size_hint_x=0.5,
            shorten=True,
            shorten_from="left",
        )
        top.add_widget(self.path_label)
        self.add_widget(top)

    def open_chooser(self, *_args):
        content = BoxLayout(orientation="vertical")
        # dirselect=False, files only; this just lists directory entries,
        # it does not open/read the file, so browsing is fast regardless
        # of how large the target file is.
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
    """Same idea as PathPickerRow but for choosing a directory (no typing)."""

    def __init__(self, title, default_path, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=70, **kwargs)
        self.title = title
        self.selected_path = default_path

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
        chooser = FileChooserListView(
            path=self.selected_path if os.path.isdir(self.selected_path) else os.path.expanduser("~"),
            dirselect=True,
        )
        content.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height=44)
        popup = Popup(title=f"Select {self.title}", content=content, size_hint=(0.9, 0.9))

        def select(*_a):
            if chooser.selection:
                self.selected_path = chooser.selection[0]
            else:
                self.selected_path = chooser.path
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
        """Called from the background worker thread. Kivy widgets must only
        be touched from the main thread, so we hop back onto it via Clock."""
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
            except Exception as e:
                self.append_log_threadsafe(f"\n✗ ERROR: {e}")
            finally:
                Clock.schedule_once(lambda _dt: self._reset_run_button(), 0)

        # Run on a background thread so picking/processing huge (MB-to-GB)
        # files never freezes the UI; progress still streams in live via
        # the log callback above.
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
