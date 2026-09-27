import os
import array
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


class AndroidDirectoryPicker:
    """Launches Android's native folder picker (Intent.ACTION_OPEN_DOCUMENT_TREE)
    - the same 'Open from' UI used for files, but for choosing a writable
    folder. Returns a persistable tree URI string (content://...), not a
    filesystem path - writing into it has to go through the Storage Access
    Framework (see create_or_replace_tree_document / AndroidTreeFile
    below), since it may not correspond to any path this app can open()
    directly."""

    _request_code_counter = 9300

    def __init__(self):
        self._callback = None
        self._request_code = None
        if ANDROID:
            activity.bind(on_activity_result=self._on_activity_result)

    def pick(self, on_picked, on_error=None):
        """on_picked(tree_uri_str) is called on success (main thread).
        on_error(message) is called on failure/cancel."""
        self._callback = on_picked
        self._error_callback = on_error
        AndroidDirectoryPicker._request_code_counter += 1
        self._request_code = AndroidDirectoryPicker._request_code_counter

        intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        mActivity.startActivityForResult(intent, self._request_code)

    def _on_activity_result(self, request_code, result_code, data):
        if request_code != self._request_code:
            return
        if data is None:
            if self._error_callback:
                self._error_callback("No folder selected")
            return

        tree_uri = data.getData()
        try:
            flags = (
                Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            mActivity.getContentResolver().takePersistableUriPermission(tree_uri, flags)
        except Exception:
            pass  # not fatal - the grant is usually still valid for this session

        try:
            uri_str = tree_uri.toString()
            Clock.schedule_once(lambda _dt: self._callback(uri_str), 0)
        except Exception as e:
            if self._error_callback:
                Clock.schedule_once(lambda _dt: self._error_callback(str(e)), 0)


def is_tree_uri(output_dir):
    return ANDROID and isinstance(output_dir, str) and output_dir.startswith("content://")


def friendly_tree_name(tree_uri_str):
    """Best-effort human-readable label for a SAF tree URI, e.g.
    'primary:Download' -> 'Download'."""
    try:
        Uri = autoclass("android.net.Uri")
        DocumentsContract = autoclass("android.provider.DocumentsContract")
        doc_id = DocumentsContract.getTreeDocumentId(Uri.parse(tree_uri_str))
        if ":" in doc_id:
            _, _, path_part = doc_id.partition(":")
            return path_part or doc_id
        return doc_id
    except Exception:
        return tree_uri_str


def _find_existing_child_id(resolver, tree_uri, parent_doc_id, display_name):
    DocumentsContract = autoclass("android.provider.DocumentsContract")
    children_uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree_uri, parent_doc_id)
    cursor = resolver.query(children_uri, None, None, None, None)
    if cursor is None:
        return None
    try:
        name_idx = cursor.getColumnIndex("_display_name")
        id_idx = cursor.getColumnIndex("document_id")
        while cursor.moveToNext():
            if name_idx == -1 or id_idx == -1:
                continue
            if cursor.getString(name_idx) == display_name:
                return cursor.getString(id_idx)
    finally:
        cursor.close()
    return None


def create_or_replace_tree_document(tree_uri_str, display_name, mime_type="text/plain"):
    """Creates display_name inside the SAF tree, deleting any existing
    document with the same name first so re-running the tool overwrites
    the previous result instead of Android silently creating
    'result (1).txt'. Returns the new document's Uri."""
    Uri = autoclass("android.net.Uri")
    DocumentsContract = autoclass("android.provider.DocumentsContract")
    resolver = mActivity.getContentResolver()
    tree_uri = Uri.parse(tree_uri_str)
    parent_doc_id = DocumentsContract.getTreeDocumentId(tree_uri)
    parent_doc_uri = DocumentsContract.buildDocumentUriUsingTree(tree_uri, parent_doc_id)

    existing_id = _find_existing_child_id(resolver, tree_uri, parent_doc_id, display_name)
    if existing_id:
        existing_uri = DocumentsContract.buildDocumentUriUsingTree(tree_uri, existing_id)
        DocumentsContract.deleteDocument(resolver, existing_uri)

    return DocumentsContract.createDocument(resolver, parent_doc_uri, mime_type, display_name)


class AndroidTreeFile:
    """File-like wrapper around a SAF document's OutputStream, so code that
    writes with plain open()/.write()/.writelines() doesn't need to change
    based on whether output_dir is a real path or a picked SAF tree."""

    def __init__(self, resolver, doc_uri):
        self._stream = resolver.openOutputStream(doc_uri)

    def write(self, text):
        self._stream.write(bytearray(text.encode("utf-8")))

    def writelines(self, lines):
        self.write("".join(lines))

    def close(self):
        self._stream.flush()
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def open_output_file(output_dir, filename):
    """Returns a writable, context-manager-capable file-like object for
    filename inside output_dir - a plain open() for a normal filesystem
    path, or a SAF-backed AndroidTreeFile if output_dir is a picked
    content:// tree URI."""
    if is_tree_uri(output_dir):
        doc_uri = create_or_replace_tree_document(output_dir, filename)
        resolver = mActivity.getContentResolver()
        return AndroidTreeFile(resolver, doc_uri)
    os.makedirs(output_dir, exist_ok=True)
    return open(os.path.join(output_dir, filename), "w", encoding="utf-8")


def output_display_path(output_dir, filename):
    """A string safe to show in the log/UI for filename inside output_dir -
    there's no real filesystem path to show for a SAF tree URI."""
    if is_tree_uri(output_dir):
        return f"{filename} (in selected folder)"
    return os.path.join(output_dir, filename)


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
            # pyjnius auto-converts a Python bytearray to a Java byte[]
            # when passed to a method expecting one, including as the
            # mutable out-parameter InputStream.read(byte[]) fills in
            # place - no manual Java array allocation needed. The previous
            # attempts (java.lang.reflect.Array.newInstance, then
            # autoclass('[B')(CHUNK)) both tried to construct a Java array
            # object directly, which pyjnius doesn't expose a constructor
            # for - hence "No constructor" on every single pick.
            buf = bytearray(CHUNK)
            with open(local_path, "wb") as out_f:
                while True:
                    n = input_stream.read(buf)
                    if n == -1:
                        break
                    out_f.write(bytes(buf[:n]))
            input_stream.close()

            Clock.schedule_once(lambda _dt: self._callback(local_path), 0)
        except Exception as e:
            err = f"Failed to read picked file: {e}"
            if self._error_callback:
                Clock.schedule_once(lambda _dt: self._error_callback(err), 0)


def _count_lines_fast(path):
    """Counts lines without holding any line content in memory - safe for
    GB-scale files. Binary mode: counting raw newline-terminated chunks is
    both faster and avoids decoding bytes we're about to throw away."""
    count = 0
    with open(path, "rb") as f:
        for _ in f:
            count += 1
    return count


class IndexedLineFile:
    """Random access to an arbitrary line of a large text file without
    ever holding its content in memory. Reading the whole file into a
    Python list (the original f.readlines() approach) needs memory on
    the order of the file's size - fine at 110k lines, but a non-starter
    at multi-GB scale on a phone. Instead this builds a SPARSE index (one
    byte offset every `stride` lines) in a single forward pass - e.g. for
    a file with 50 million lines and stride=256, that's ~195k stored
    offsets (a couple MB), not 50 million lines of text. A random line
    lookup re-opens the file, seeks to the nearest checkpoint, and reads
    forward at most `stride` lines - bounded, cheap work per lookup."""

    def __init__(self, path, stride=256):
        self.path = path
        self.stride = stride
        self._checkpoints = array.array("q")
        self._line_count = 0
        self._build_index()

    def _build_index(self):
        offset = 0
        count = 0
        with open(self.path, "rb") as f:
            self._checkpoints.append(0)
            for raw_line in f:
                count += 1
                offset += len(raw_line)
                if count % self.stride == 0:
                    self._checkpoints.append(offset)
        self._line_count = count

    def __len__(self):
        return self._line_count

    def get_range(self, start, end):
        """1-indexed, inclusive. Returns decoded lines (with their
        trailing newline, matching f.readlines() behavior) as a list -
        one seek to the nearest checkpoint, then a single sequential
        read, rather than one seek per line."""
        if start < 1 or end > self._line_count or start > end:
            raise IndexError(f"line range {start}-{end} out of bounds (1-{self._line_count})")
        idx = (start - 1) // self.stride
        start_offset = self._checkpoints[idx]
        lines_to_skip = (start - 1) - idx * self.stride
        n = end - start + 1
        result = []
        with open(self.path, "rb") as f:
            f.seek(start_offset)
            for _ in range(lines_to_skip):
                f.readline()
            for _ in range(n):
                result.append(f.readline().decode("utf-8"))
        return result


class _MaxBIT:
    """Fenwick tree supporting 'max end value among all intervals whose
    start is <= i' in O(log n), used to detect file1-range overlaps
    without comparing every new mapping against every previous one."""

    def __init__(self, size):
        self.size = max(size, 1)
        self.tree = [0] * (self.size + 1)

    def update(self, i, value):
        i = min(max(i, 1), self.size)
        while i <= self.size:
            if self.tree[i] < value:
                self.tree[i] = value
            i += i & (-i)

    def query_prefix_max(self, i):
        i = min(max(i, 0), self.size)
        res = 0
        while i > 0:
            if self.tree[i] > res:
                res = self.tree[i]
            i -= i & (-i)
        return res


class ReplacementEngine:
    """Holds the original line-replacement logic, unchanged in behavior,
    just adapted to run as a method that reports progress via self.log()
    instead of print(), and takes file paths as arguments instead of
    hardcoded constants."""

    def __init__(self, log_callback):
        self.log = log_callback

    def run(self, file1_path, file2_path, map_path, output_dir):
        # File paths (or display strings, if output_dir is a picked SAF
        # tree URI rather than a real filesystem path)
        output_path = output_display_path(output_dir, "result.txt")
        log_path = output_display_path(output_dir, "replacement_log.txt")

        self.log("="*60)
        self.log("LINE REPLACEMENT TOOL")
        self.log("="*60)
        
        # file1 and file2 are NOT loaded into memory here. Doing that with
        # f.readlines() needs memory on the order of the file's size (or
        # more, given Python's per-string object overhead) - fine at
        # ~100k lines, but not viable for a multi-GB file on a phone.
        # file1 is only ever consumed in increasing line order later on
        # (see [4/5] below), so it just needs a cheap line count here.
        # file2's ranges can be referenced in any order, so it gets a
        # sparse index (see IndexedLineFile) that allows jumping to any
        # line without ever holding the file's content in memory.
        self.log("\n[1/5] Reading input files...")
        num_file1_lines = _count_lines_fast(file1_path)
        self.log(f"  ✓ File1: {num_file1_lines} lines")

        file2_index = IndexedLineFile(file2_path)
        num_file2_lines = len(file2_index)
        self.log(f"  ✓ File2: {num_file2_lines} lines")
        
        # Read and parse the map file. The map file just holds compact
        # line-range directives (not the data itself), so it's normally
        # tiny even when file1/file2 are huge - but it's still streamed
        # rather than loaded with readlines(), in case someone does end
        # up with an enormous number of mappings.
        self.log("\n[2/5] Parsing and validating map file...")
        replacements = []
        log_entries = []
        validation_results = []

        total_mappings = 0
        with open(map_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() and "=" in line:
                    total_mappings += 1
        self.log(f"  ✓ Found {total_mappings} mapping(s) to process")
        
        mapping_num = 0
        overlap_index = _MaxBIT(max(num_file1_lines, 1))
        map_file_lines = open(map_path, "r", encoding="utf-8")
        for line_num, line in enumerate(map_file_lines, 1):
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
                if start1 < 1 or end1 > num_file1_lines:
                    errors.append(f"File1 range {start1}-{end1} is out of bounds (1-{num_file1_lines})")
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
                    if start2 < 1 or end2 > num_file2_lines:
                        errors.append(f"File2 range #{idx+1} ({start2}-{end2}) is out of bounds (1-{num_file2_lines})")
                    if start2 > end2:
                        errors.append(f"File2 range #{idx+1} ({start2}-{end2}) is invalid (start > end)")
                
                # Check for overlapping ranges in file1. A prefix-max
                # index answers "does anything overlap?" in O(log n); the
                # exact O(n) scan (matching the original warning text
                # verbatim) only runs on the rare mapping that actually
                # does overlap something, instead of on every mapping.
                if overlap_index.query_prefix_max(end1) >= start1:
                    for prev_start, prev_end, _ in replacements:
                        if not (end1 < prev_start or start1 > prev_end):
                            warnings.append(f"Overlaps with previous mapping at file1 lines {prev_start}-{prev_end}")
                overlap_index.update(start1, end1)
                
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
        map_file_lines.close()
        
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
            with open_output_file(output_dir, "replacement_log.txt") as f:
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
        
        # Process replacements, streaming output directly to disk instead
        # of building a Python list of every output line first - at
        # multi-GB scale that list would itself become the memory
        # problem even after fixing how the inputs are read.
        self.log("\n[4/5] Processing replacements and writing output...")
        current_line = 1
        rep_index = 0
        processed_count = 0
        total_lines_inserted = 0
        total_output_lines = 0
        processing_results = []

        with open_output_file(output_dir, "result.txt") as out_f, \
                open(file1_path, "r", encoding="utf-8") as f1:
            # file1 is consumed strictly in increasing order below (current_line
            # only ever goes up), so a single forward iterator over it is
            # all that's needed - no need to hold its lines in memory or
            # seek backward.
            file1_iter = iter(f1)

            while current_line <= num_file1_lines:
                # Check if current_line is start of any replacement range
                if rep_index < len(replacements) and current_line == replacements[rep_index][0]:
                    start1, end1, file2_ranges = replacements[rep_index]

                    # These file1 lines are being replaced - consume
                    # (discard) them from the stream without writing them.
                    for _ in range(end1 - start1 + 1):
                        next(file1_iter, None)

                    lines_inserted = 0
                    # Append lines from all file2 ranges
                    for start2, end2 in file2_ranges:
                        chunk = file2_index.get_range(start2, end2)
                        out_f.writelines(chunk)
                        lines_inserted += len(chunk)
                    total_output_lines += lines_inserted

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
                    line = next(file1_iter, None)
                    if line is None:
                        break
                    out_f.write(line)
                    total_output_lines += 1
                    current_line += 1
        
        self.log(f"  ✓ All {processed_count} replacement(s) completed successfully")
        self.log(f"  ✓ Output saved to: {output_path}")
        self.log(f"  ✓ Total lines in output: {total_output_lines}")

        # Write detailed log
        self.log("\n[5/5] Generating log file...")
        with open_output_file(output_dir, "replacement_log.txt") as f:
            f.write("="*60 + "\n")
            f.write("LINE REPLACEMENT LOG - SUCCESS\n")
            f.write("="*60 + "\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("INPUT FILES:\n")
            f.write(f"  File1: {file1_path} ({num_file1_lines} lines)\n")
            f.write(f"  File2: {file2_path} ({num_file2_lines} lines)\n")
            f.write(f"  Map:   {map_path} ({total_mappings} mappings)\n\n")
            
            f.write("OUTPUT:\n")
            f.write(f"  Result: {output_path} ({total_output_lines} lines)\n\n")
            
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
        self.log(f"✓ Output file: {total_output_lines} total lines")
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
    """Directory picker. On Android this opens the real system folder
    picker (Intent.ACTION_OPEN_DOCUMENT_TREE - the same 'Open from' UI
    used for files, just for folders), matching how File 1/2/Map already
    behave. Elsewhere (desktop testing) it falls back to Kivy's own file
    chooser with dirselect enabled. No manual typing."""

    def __init__(self, title, default_path, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=70, **kwargs)
        self.title = title
        # Create the default output dir immediately (used as-is unless the
        # user picks a different folder, and as the desktop chooser's
        # starting point) - otherwise os.path.isdir(self.selected_path)
        # below is False the first time the picker opens.
        try:
            os.makedirs(default_path, exist_ok=True)
        except Exception:
            pass
        self.selected_path = default_path
        self.selected_display = default_path
        self._fallback_base = os.path.dirname(default_path) or default_path
        self._android_picker = AndroidDirectoryPicker() if ANDROID else None

        top = BoxLayout(size_hint_y=None, height=44, spacing=8)
        top.add_widget(Label(text=title, size_hint_x=0.25))
        self.pick_btn = Button(text="Pick folder", size_hint_x=0.25)
        self.pick_btn.bind(on_release=self.open_chooser)
        top.add_widget(self.pick_btn)
        self.path_label = Label(
            text=self.selected_display, size_hint_x=0.5, shorten=True, shorten_from="left"
        )
        top.add_widget(self.path_label)
        self.add_widget(top)

    def open_chooser(self, *_args):
        if ANDROID:
            self.pick_btn.disabled = True
            self.path_label.text = "Opening file manager..."
            self._android_picker.pick(
                on_picked=self._on_android_picked,
                on_error=self._on_android_error,
            )
        else:
            self._open_kivy_chooser()

    def _on_android_picked(self, tree_uri_str):
        self.pick_btn.disabled = False
        self.selected_path = tree_uri_str
        self.selected_display = friendly_tree_name(tree_uri_str)
        self.path_label.text = self.selected_display

    def _on_android_error(self, message):
        self.pick_btn.disabled = False
        self.path_label.text = f"Error: {message}" if message else self.selected_display

    def _open_kivy_chooser(self):
        try:
            content = BoxLayout(orientation="vertical")
            start_path = (
                self.selected_path if os.path.isdir(self.selected_path) else self._fallback_base
            )
            try:
                chooser = FileChooserListView(path=start_path, dirselect=True)
            except Exception as e:
                # Listing start_path failed (e.g. no permission) - show
                # that instead of doing nothing, and fall back to a path
                # the app definitely owns.
                self.path_label.text = f"Error opening folder picker: {e}"
                chooser = FileChooserListView(path=self._fallback_base, dirselect=True)
            content.add_widget(chooser)
            buttons = BoxLayout(size_hint_y=None, height=44)
            popup = Popup(title=f"Select {self.title}", content=content, size_hint=(0.9, 0.9))

            def select(*_a):
                self.selected_path = chooser.selection[0] if chooser.selection else chooser.path
                self.selected_display = self.selected_path
                self.path_label.text = self.selected_display
                popup.dismiss()

            select_btn = Button(text="Select")
            select_btn.bind(on_release=select)
            cancel_btn = Button(text="Cancel")
            cancel_btn.bind(on_release=popup.dismiss)
            buttons.add_widget(select_btn)
            buttons.add_widget(cancel_btn)
            content.add_widget(buttons)
            popup.open()
        except Exception as e:
            # Whatever else goes wrong (fallback chooser too, popup
            # construction, etc.) - show it. A tap that produces nothing
            # visible is what got us here in the first place.
            self.path_label.text = f"Error: {e}"

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

        self._log_buffer = []
        self._log_lock = threading.Lock()
        # Flush a few times a second instead of once per log line. A
        # TextInput re-renders its ENTIRE contents (layout, cursor,
        # glyphs) on every text change, so updating it once per line - as
        # this used to do via Clock.schedule_once per call - turned a run
        # with thousands of log lines (e.g. one per mapping during
        # validation) into tens of thousands of full-widget re-renders,
        # each one slower than the last as the text grew. That's what
        # looked like the app "stopping in the middle": it was still
        # running, just falling further and further behind.
        Clock.schedule_interval(self._flush_log, 0.15)

    def append_log_threadsafe(self, message=""):
        """Safe to call from the background worker thread - just buffers
        the line; _flush_log applies buffered lines to the widget in
        batches on the main thread."""
        with self._log_lock:
            self._log_buffer.append(str(message))

    def _flush_log(self, _dt):
        with self._log_lock:
            if not self._log_buffer:
                return
            chunk = "\n".join(self._log_buffer) + "\n"
            self._log_buffer.clear()
        self.log_output.text += chunk
        # Cap how much text stays on screen - the widget's own cost to
        # hold/redraw its content grows with total length, so an
        # unbounded log would keep getting slower for the rest of a long
        # run even with batching. The full detail is always in the saved
        # log file regardless of what's kept on screen here.
        MAX_CHARS = 200_000
        if len(self.log_output.text) > MAX_CHARS:
            self.log_output.text = (
                "...(earlier output trimmed - see the saved log file)...\n"
                + self.log_output.text[-MAX_CHARS:]
            )

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
        with self._log_lock:
            self._log_buffer.clear()
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
