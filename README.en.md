# <img src="Mecro_Tomacro.png" width="32" alt="tomacro icon"> tomacro — Clip Studio ↔ Modeler Texture Macro

[한국어](README.md) | **English**

A macro program that automates the repetitive task of pasting textures edited in
Clip Studio Paint into the Material tab of Clip Studio Modeler.

Unlike TinyTask, **it supports Alt+Tab window switching.**
When you press Alt+Tab while recording, the program records *which window you
switched to* instead of the keystrokes, and during playback it activates that
window directly via the Windows API. This means playback switches to the correct
program even if the window order has changed since recording.

## How to Run

Download `tomacro_deploy.zip` from [Releases](../../releases), unzip it, and run
`tomacro.exe` — no installation required.

To run from source: `python tomacro.py` (requires Python 3.x + `pynput`)

## Usage

| Key | Function |
|---|---|
| **F9** | Start / stop recording |
| **F10** | Start playback |
| **ESC** | Stop playback |

1. Press F9 and perform the task once as you normally would.
   (Copy a texture in Clip Studio → Alt+Tab → paste into Modeler's Material tab → …)
2. Press F9 again to finish recording.
3. Click **"Save Recording"** and give it a name (e.g. `two_materials`, `base_30`).
4. Open the next work file and press F10 — the same task replays automatically.

## When Positions Differ per Material Count

Click positions change depending on the material layout (1 material / 2 materials / …),
so **record a separate macro for each layout**, save them under different names,
then double-click the matching macro in the list to load it and press F10 to play.

## Playback Settings

- **Repeat count**: how many times to repeat the macro
- **Gap (sec)**: wait time between repeats
- **Speed**: 0.5 (slower) to 3.0 (faster). If pastes get dropped, lower it to 0.8 or below.

## Language

Five languages are supported: 한국어 · English · 日本語 · 中文 · Español.
Pick one from the dropdown in the top-right corner of the window. On first launch
the language is selected automatically based on your Windows display language.

## Notes

- Window positions, sizes, and panel layouts **must be the same** when recording
  and when playing back. (Running both programs maximized is recommended.)
- If you change the screen resolution or scaling (e.g. 125%), re-record your macros.
- If Clip Studio is run **as administrator**, the macro cannot send input to it.
  Run both programs with normal privileges.
- Don't touch the mouse or keyboard during playback — press ESC first to stop.

## Requirements

- Windows 10/11
- Python 3.x + `pynput` (`pip install pynput`)
  (the distributed `tomacro.exe` runs without any installation)

## For Developers: Macro File Backward Compatibility (Important)

Users' recorded macros (`macros/*.json`) are their work assets.
**Macro files saved by previous versions must keep working after every update.**

- The file format is `{"version": 1, "events": [...]}` with event types
  `focus` / `move` / `click` / `scroll` / `key_down` / `key_up`.
- **Never rename, repurpose, or remove existing fields.** If you need new features,
  only *add* new event types or optional fields.
- If a format change is unavoidable: bump `version` and ship **migration code that
  automatically converts old files**.
- Before each release, test that a json saved by the previous version still loads
  and plays in the new version.

## Credits

- **Teummailer (틈메이러)** — feedback, direction, and the program icon. Thank you!
