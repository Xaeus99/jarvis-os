# JARVIS without Tab: the offline brain

JARVIS OS works as a complete voice assistant on its own. No Tab link, no account, no API key and nothing paid.
Linking to Tab is optional and adds the bigger jobs (email, calendar, texting people, orders, live news).

## What happens when you talk to it

```
"Hey Jarvis" (wake word)  or  Ctrl+Shift+Space (push to talk)  or  typing in the workspace panel (Ctrl+Shift+J)
        |
speech-to-text on this PC (faster-whisper base.en, ships in the image; works offline from the first boot)
        |
1. built-in commands   open/close apps, volume and mute, play/pause/next, time and date, timers and alarms,
                       weather (no key needed), screenshots, lock/sleep, game and stream mode, OBS (go live,
                       record, clip that, scenes), lights, updates, switch to Windows ... ("what can you do")
2. the offline brain   everything else: questions, explanations, homework help, jokes, chat. A free local AI model
                       (Ollama) on this PC. It remembers the last few turns ("forget our conversation" clears it).
3. Tab (if linked)     jobs that need the internet or your accounts: email, calendar, texts, orders, news, prices
        |
JARVIS's voice (Piper, en_GB "alan", ships in the image; espeak-ng if Piper ever fails)
```

The offline brain answers as J.A.R.V.I.S.: calls you "sir", keeps answers to a few spoken sentences, stays
age-appropriate, and politely declines anything that isn't. It runs offline, so for live facts it says it may be
out of date (or, when linked, Tab handles them).

## Getting the offline brain (first login, about 3 GB, once)

The speech model and the voice are part of JARVIS OS. The AI model is not (it would make the installer several GB
bigger), so on the first login JARVIS downloads it in the background and shows progress notifications:

1. the AI runtime (Ollama, a pinned version, about 1.4 GB, checksum-verified), then
2. the model: **llama3.2:3b** (about 2 GB) on a PC with an NVIDIA GPU or 12 GB+ of RAM, otherwise
   **llama3.2:1b** (about 1.3 GB), which suits virtual PCs and small machines.

Everything goes into your home folder (`~/.local/share/jarvis/ollama`), never into the read-only system, and the AI
server only listens on this PC (127.0.0.1). An NVIDIA card is used automatically (Ollama brings its own CUDA
runtime; RTX 50-series included); without one it runs on the CPU, slower but fine for short answers.

- Say **"Jarvis, download your offline brain"**, or run `jarvisctl ai setup`, to start or resume the download.
- **"Jarvis, is your offline brain ready?"** or `jarvisctl ai status` says how far it is.
- `jarvisctl ai ask "why is the sky blue"` asks it from a terminal.
- A stopped download resumes where it left off at the next login.

Settings (`~/.config/jarvis/config.json`, all optional):

```json
{"assistant": {"local_ai": true, "local_ai_model": "", "local_ai_url": "http://127.0.0.1:11434"}}
```

`local_ai: false` switches it off (and skips the download). `local_ai_model` picks any model from the Ollama
library, for example `"qwen2.5:3b"`; it's downloaded the next time you say "download your offline brain".
Removing it: `systemctl --user disable --now jarvis-ollama` and delete `~/.local/share/jarvis/ollama`.

## The race: several free models answer at once

On a PC with an NVIDIA graphics card, or with 12 GB of memory or more, JARVIS keeps three free models and asks all of
them at the same time: `llama3.2:1b`, `llama3.2:3b` and `qwen2.5:3b`. The first one to finish a good first sentence
wins, the others are stopped, and JARVIS speaks each sentence the moment it's complete instead of waiting for the whole
answer. While a game is running only the small `llama3.2:1b` answers, so the game keeps the graphics card.

The first model downloads at first login (JARVIS can chat as soon as it's in); the other two and the coding model
follow in the background. On a small virtual PC (under 12 GB) JARVIS uses only `llama3.2:1b`. Every answer's timing is
logged to `~/.local/state/jarvis/ai_latency.jsonl`.

- "Jarvis, is your offline brain ready?" says how many models are racing.
- `jarvisctl ai race "why is the sky blue"` prints each sentence with its time and the winner.
- Settings (`~/.config/jarvis/config.json`): `"assistant": {"local_ai_models": ["llama3.2:1b", "qwen2.5:3b"]}` picks the racers.

## Making things: code and pictures (free, on this PC)

- **Code:** "Jarvis, write a python script that renames my screenshots" (or "code me a …"). A local coding model
  (`qwen2.5-coder:3b`, about 1.9 GB, downloaded the first time) writes one file into `~/Documents/JARVIS Code/` and the
  JARVIS viewer opens it with syntax colours, **Copy**, **Save as**, **Open in editor** and **Open folder**.
  JARVIS never runs code it wrote, and the file isn't executable. Bigger builds (a whole app or website) go to Tab when
  JARVIS is linked.
- **Pictures:** "Jarvis, make an image of a red sports car at sunset", then "another one". It uses stable-diffusion.cpp
  with SD-Turbo (about 2.1 GB, downloaded the first time into `~/.local/share/jarvis/imagegen`). With an NVIDIA card it
  runs on the card through Vulkan (no CUDA install) and takes seconds; without one it uses the processor and takes a
  minute or two. Before painting, JARVIS unloads the chat models so the picture model fits in graphics memory, and it
  won't paint while a game is running. Pictures go to `~/Pictures/JARVIS Images/` and open in the viewer with **Copy**,
  **Save as**, **Open folder** and **Regenerate**.
- **Safety (always on):** sexual, nude, gory and hateful requests and pictures of real, named people are refused before
  anything is drawn; every picture gets a strict negative prompt; and every finished picture is checked by an image
  safety classifier on this PC. A picture that fails is deleted before anyone sees it. Requests for malware, cheats or
  password stealers are refused for code.
- "Set up the image engine" downloads both ahead of time; "open my images" / "open my code" open the folders;
  `jarvisctl make code|image "…"` does the same from a terminal.

## In a virtual PC (VirtualBox)

JARVIS OS runs in VirtualBox with no GPU; the offline brain uses the small model on the CPU.

- Give the VM **at least 4 GB of RAM** (8 GB is comfortable) and 4 CPUs.
- **Settings > Audio:** Audio Controller *Intel HD Audio* (or ICH AC97), and tick **Enable Audio Output** and
  **Enable Audio Input**. VirtualBox leaves input *off* by default, and then JARVIS can't hear you. On the host,
  Windows must also allow apps (VirtualBox) to use the microphone: Settings > Privacy & security > Microphone.
- Push to talk (Ctrl+Shift+Space) or typing in the workspace panel works even when the wake word is hard to hear
  through a VM.

## Linking to Tab later (optional)

On a PC where Windows JARVIS is installed, JARVIS OS links itself at first login (see INSTALL_DAY.md). Anywhere
else (a VM, a PC without Windows): text Tab "link code for JARVIS OS" and run `jarvisctl link --code XXXX-XXXX-XXXX`.
"Jarvis, link to Tab" explains the same. Until then the built-in commands and the offline brain do everything else.

## Tested

The `standalone-test` workflow boots the real image in a virtual PC with a virtual sound card, no Tab link and no
GPU, lets the first-login download run, and talks to JARVIS. It records what JARVIS says from the sound card and
transcribes it. Unit tests cover the routing, the download/verify/resume steps and the persona (`jarvisd/tests/test_localai.py`).
Not yet tested on real hardware: NVIDIA GPU speed, a real microphone and the wake word in a real room.
