---
title: Operational Learnings - Browser Obstructions, Taskbar Focus & EDA UI Automation
tags:
  - type/learnings
  - domain/browser-automation
  - domain/eda
  - tools/chrome
  - tools/gemini-web
  - tools/verdi
  - scope/mcp
aliases:
  - Browser & EDA Automation Learnings
  - Verdi UI Heuristics
created: 2026-10-25
updated: 2026-10-25
related:
  - "[[INDEX]]"
  - "[[../research/verdi_tool_features_and_ai_automation]]"
  - "[[../research/phase2_chrome_browser_tool]]"
  - "[[browser_interaction_recipes]]"
  - "[[linux_desktop_window_management]]"
---

# 🛠️ Operational Learnings: Browser Obstructions, Taskbar Focus & EDA UI Automation

This note details practical heuristics, troubleshooting discoveries, and execution patterns gathered during live Chrome and Gemini Web automation sessions, directly informing our Phase 2 Browser Tool and complex EDA tool (Synopsys Verdi) automation.

---

## 1. Browser Modal Obstruction Interception

### The Problem
During automated web navigation in Google Chrome, permission modals (e.g. `gemini.google.com wants to: Show notifications [Block] [Allow]`) appear at the top-left of the viewport. These modals capture focus and block keyboard input from reaching page form fields.

### The Solution & Calibration
1. **Accurate Button Bounding**:
   * On a $3024 \times 1898$ desktop resolution (downscaled to $1600 \times 1004$ for perception), the notification box is located at $x \in [300, 1000]$, $y \in [200, 500]$.
   * The **Block** button center is at desktop coordinates $(686, 468)$.
   * The **Allow** button center is at desktop coordinates $(850, 468)$.
2. **Delta Verification**:
   * Clicking "Block" at $(686, 468)$ produced a verified visual delta ($\Delta = 0.59\%$), instantly restoring page focus to the central chat area.

---

## 2. Window Raising via Desktop Taskbar Clicks

### The Problem
When the AI agent executes background terminal commands or Python test runners, the terminal window (`user@linux: ~/DV`) can seize active X11 input focus, rendering the Chrome browser inactive (`is_active: false`).

### The Solution
* In environments where `_NET_ACTIVE_WINDOW` ClientMessage dispatching is blocked by window manager focus-stealing prevention policies, **clicking the application's icon in the bottom desktop panel** is 100% reliable.
* In our XFCE/MATE panel ($y \in [1860, 1898]$):
  * Item 1: Terminal (`user@linux`) $\rightarrow x \in [0, 350]$
  * Item 4: DevTools $\rightarrow x \in [1150, 1400]$
  * Item 5: **Google Chrome (`Google Gemini`)** $\rightarrow x \approx 1650, y = 1879$.
* Clicking at $(1650, 1879)$ produced a **75.29% visual delta**, instantly bringing Chrome back to the absolute foreground.

---

## 3. Streaming Generation Monitoring on Gemini Web

### The Pattern
When submitting long prompts to Gemini 4 Argon, the model conducts web searches and streams multi-page technical responses:
1. **Search Phase**: Displays `•• Searching the web` animation.
2. **Streaming Phase**: The submit button transforms into a black square stop icon (`[■]`) at the bottom-right of the prompt bar.
3. **Completion Phase**: The black stop icon reverts back to the microphone icon (`[🎙]`) and the browser tab title updates from `"Google Gemini"` to the generated conversation title (e.g., `"Synopsys Verdi ASIC/SoC Debug Guide - Google Chrome"`).
4. **Heuristic**: Agents should poll for the disappearance of the stop icon or the browser tab title change before initiating post-generation page scrolling.

---

## 4. Multi-Page Web & EDA Content Scrolling

### The Pattern
* Keyboard `Page_Down` can be ignored if keyboard focus remains inside the text input box.
* **Reliable Scroll Strategy**:
  1. Reposition mouse pointer directly over the main content viewport: `move_cursor(x=1500, y=500)`.
  2. Issue mouse wheel scroll commands: `scroll(direction="down", amount=15)`.
  3. Every 15-unit scroll step advances the viewport by approximately one full visual page with a consistent **$14\% - 16\%$ visual delta**.

---

## 5. Architectural Heuristics for Complex EDA UIs (Verdi)

From our Gemini Argon research, the following engineering rules apply when automating Synopsys Verdi:
1. **Window Docking**: Always trigger `Window -> Dock All` upon launching Verdi to eliminate overlapping floating tool windows (`nTrace`, `nWave`, `nSchema`).
2. **Coordinate-Free Hotkeys**: Prefer sending keyboard shortcuts (`X` for annotation, `F` for zoom full, `D` for driver trace, `Ctrl+W` for waveform export) after sub-pane focus rather than clicking tiny toolbar buttons.
3. **Value Column OCR**: Keep `View -> Signal Value` enabled in `nWave` so that signal values are read as structured numerical text rather than trying to visually decode analog or binary transitions on waveform pixels.
