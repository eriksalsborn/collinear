#!/usr/bin/env python3
"""Install Collinear into your herdr.

    python3 install.py                               ask before anything it cannot decide itself
    python3 install.py --issues-key prefix+alt+l     choose the keys yourself
                       --prs-key prefix+alt+g

What it does, and nothing else:
  1. copies issues.py, prs.py and prompts/ next to your herdr config (~/.config/herdr)
  2. creates local_settings.json there from the example, unless you already have one
  3. adds one key for each popup to your config.toml (a copy of it is kept as config.toml.before-collinear),
     on keys you are not already using
  4. asks herdr to check the config and reload it
Running it again updates the code and leaves your settings and keys alone."""
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path

SOURCE_FOLDER = Path(__file__).resolve().parent
HERDR_FOLDER = Path.home() / ".config" / "herdr"
CONFIG_FILE = HERDR_FOLDER / "config.toml"
POPUPS = [  # (script, preferred key, what the key does, popup height)
    ("issues.py", "prefix+alt+i", "start work on a Linear issue", "80%"),
    ("prs.py", "prefix+alt+p", "open PRs", "80%"),
]


def key_is_used(config_text, key):
    return f'"{key}"' in config_text


def popup_is_installed(config_text, script):
    return re.search(rf'command\s*=\s*".*{re.escape(script)}"', config_text) is not None


def keybinding_block(script, key, description, height):
    return (f'\n[[keys.command]]\nkey = "{key}"\ntype = "popup"\ncommand = "python3 ~/.config/herdr/{script}"\n'
            f'description = "{description}"\nwidth = "90%"\nheight = "{height}"\n')


def choose_key(config_text, script, preferred_key, key_from_command_line):
    """The key for one popup: the one given on the command line, else the preferred one when it is free, else ask."""
    if key_from_command_line:
        if key_is_used(config_text, key_from_command_line):
            sys.exit(f"{key_from_command_line} is already used in {CONFIG_FILE}. Pick another key for {script}.")
        return key_from_command_line
    if not key_is_used(config_text, preferred_key):
        return preferred_key
    if not sys.stdin.isatty():
        sys.exit(f"{preferred_key} is already used in your config.toml. Run again with a free key, for example:\n"
                 f"  python3 install.py --{'issues' if script == 'issues.py' else 'prs'}-key prefix+alt+l")
    while True:
        key = input(f"{preferred_key} is already used in your config.toml. Which key should open {script}? (for example prefix+alt+l) ").strip()
        if key and not key_is_used(config_text, key):
            return key
        print(f"  {key or 'that'} is taken too." if key else "  type a key, like prefix+alt+l")


def main():
    parser = argparse.ArgumentParser(description="Install Collinear into your herdr.")
    parser.add_argument("--issues-key", help="key that opens the issue popup (default: prefix+alt+i when it is free)")
    parser.add_argument("--prs-key", help="key that opens the PR popup (default: prefix+alt+p when it is free)")
    arguments = parser.parse_args()
    keys_from_command_line = {"issues.py": arguments.issues_key, "prs.py": arguments.prs_key}

    HERDR_FOLDER.mkdir(parents=True, exist_ok=True)
    if SOURCE_FOLDER != HERDR_FOLDER.resolve():
        for script, *_ in POPUPS:
            shutil.copy(SOURCE_FOLDER / script, HERDR_FOLDER / script)
        shutil.copytree(SOURCE_FOLDER / "prompts", HERDR_FOLDER / "prompts", dirs_exist_ok=True)
        print(f"✓ copied issues.py, prs.py and prompts/ to {HERDR_FOLDER}")
    settings_file = HERDR_FOLDER / "local_settings.json"
    if settings_file.exists():
        print("✓ kept your local_settings.json")
    else:
        shutil.copy(SOURCE_FOLDER / "local_settings.example.json", settings_file)
        print("✓ created local_settings.json (the setup screen can change the repositories folder: F)")

    config_text = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else ""
    blocks_to_add, keys_chosen = "", {}
    for script, preferred_key, description, height in POPUPS:
        if popup_is_installed(config_text, script):
            print(f"✓ {script} already has a key in config.toml")
            continue
        key = choose_key(config_text + blocks_to_add, script, preferred_key, keys_from_command_line[script])
        blocks_to_add += keybinding_block(script, key, description, height)
        keys_chosen[script] = key
    if blocks_to_add:
        backup_file = CONFIG_FILE.with_name("config.toml.before-collinear")
        if CONFIG_FILE.exists() and not backup_file.exists():
            shutil.copy(CONFIG_FILE, backup_file)
        CONFIG_FILE.write_text(config_text.rstrip("\n") + "\n" + blocks_to_add if config_text else blocks_to_add.lstrip("\n"))
        for script, key in keys_chosen.items():
            print(f"✓ {key} opens {script}")

    for herdr_command, what_it_is in ((["herdr", "config", "check"], "config check"), (["herdr", "server", "reload-config"], "reload")):
        try:
            result = subprocess.run(herdr_command, capture_output=True, text=True, timeout=20)
            said = (result.stdout or result.stderr).strip().splitlines()
            summary = said[-1] if said else "done"
            if summary.startswith("{"):  # the reload answers in JSON: say just how it went
                summary = (json.loads(summary).get("result") or {}).get("status", "done")
            print(f"{'✓' if result.returncode == 0 else '!'} herdr {what_it_is}: {summary}")
        except (OSError, subprocess.TimeoutExpired):
            print(f"! could not run herdr for the {what_it_is}: is herdr installed and running? Start it and run this again.")

    issues_key = keys_chosen.get("issues.py", "the key you gave the issue popup")
    print(f"\nOpen herdr and press {issues_key}. The first time, Col shows a short checklist and says how to fix each line.")


if __name__ == "__main__":
    main()
