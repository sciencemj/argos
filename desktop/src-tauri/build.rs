// The Argos pages come from the local server (a remote URL to Tauri), which may only call
// app commands that have a permission; the manifest generates `allow-<command>` for each,
// granted in capabilities/default.json.
const COMMANDS: &[&str] = &[
    "notify",
    "hide_quick",
    "open_main",
    "open_attachment",
    "update_status",
    "check_update",
    "restart_to_update",
    "uninstall",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
