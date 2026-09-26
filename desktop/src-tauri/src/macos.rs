//! The few AppKit calls the app needs: which app the user was in (so closing quick
//! capture goes back there) and moving files to the Trash (uninstall stays recoverable).

use std::path::Path;

use objc2_app_kit::{NSApplicationActivationOptions, NSRunningApplication, NSWorkspace};
use objc2_foundation::{NSFileManager, NSString, NSURL};

/// The process id of the app in front right now.
pub fn frontmost_pid() -> Option<i32> {
    NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .map(|app| app.processIdentifier())
}

/// Brings that app back to the front; false when it has quit meanwhile.
pub fn activate(pid: i32) -> bool {
    NSRunningApplication::runningApplicationWithProcessIdentifier(pid)
        .map(|app| app.activateWithOptions(NSApplicationActivationOptions::empty()))
        .unwrap_or(false)
}

/// Moves a file or folder to the Trash. A path that does not exist is fine.
pub fn trash(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    let url = NSURL::fileURLWithPath(&NSString::from_str(&path.to_string_lossy()));
    NSFileManager::defaultManager()
        .trashItemAtURL_resultingItemURL_error(&url, None)
        .map_err(|e| format!("{}: {}", path.display(), e.localizedDescription()))
}
