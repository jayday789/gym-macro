# Gym Macro

made by **starlingz**

automated workout macro for the roblox gym game. handles working out, eating, shakers, and more while you afk.

## quick start

1. install [Python 3.10+](https://python.org) (check "Add to PATH" during install)
2. double-click `Launch Gym Macro.bat` (installs packages automatically first time)
3. in the GUI: select your monitor, set your keybinds, choose a workout
4. add template screenshots to `templates/` folder (see below)
5. hit Start

## templates

the macro uses screenshots to recognize whats on screen. you need to capture these from YOUR game and save them as png files in the `templates/` folder.

**required:**
- `machine_prompt.png` — the "Use Machine" or "Press E" prompt
- `maintaining.png` — the green "Maintaining" status text under your name

**for workout selection:**
- `workout_*.png` — screenshot of the exercise name in the menu (e.g. `workout_squat.png`)
- `hypertrophy.png` / `strength.png` / `junk.png` — the mode indicator that shows when exercising

**optional (for extra features):**
- `deficit.png` / `cutting.png` — status texts to stop the macro
- `full_stamina.png` — full stamina bar (speeds up regen detection)
- `food_chicken.png` — chicken in inventory (for auto eating)
- `food_shaker.png` — shaker in inventory
- `food_creatine.png` — creatine in shaker popup
- `food_preworkout.png` — pre workout in shaker popup
- `empty_circle.png` — empty circle in shaker UI
- `drink_button.png` — green confirm button
- `buy_chicken.png` — chicken in shop (for bulk buying)
- `current_weight.png` — "Current weight" text (for OCR weight reading)
- `select_exercise_menu.png` — menu header (for detecting stuck menus)
- `close_x.png` — X button to close menus
- `server_boost.png` — server boost text (for XP tracking)

## how to capture templates

1. open the game and get to the screen you want to capture
2. use the **Debug** tab in the macro GUI → "Capture screen now"
3. open the saved screenshot and crop just the element you need
4. save it as a png in the `templates/` folder with the right name

**tips:**
- crop tightly around just the text/icon
- capture at the same resolution you play at
- use the Debug tab → "Test match" to verify your template works

## workout modes

- **Hypertrophy** — rapid clicks until stamina runs out, regens, repeats
- **Strength** — same fast clicking with stamina stall detection
- **Junk** — farms crew XP indefinitely, optional no-food mode

## features

- auto eating when maintaining status detected
- creatine shaker every X minutes (junk mode)
- pre workout shaker every X minutes (all modes)
- bulk buy food from shop
- discord webhook alerts (stuck, disconnect, status changes)
- progress reports with sets, reps, weight, XP, and time
- 2x crew XP gamepass + server boost tracking
- anti-stuck watchdog with zoom and disconnect detection
- type-a-word captcha solving

## settings

everything is configurable in the GUI. settings save automatically. key things to set up:
- **monitor** — which screen the game is on
- **keybinds** — interact (E), workout action (LMB), exit (Space)
- **confidence** — how strict template matching is (lower = more lenient)
- **webhook URL** — paste your discord webhook for notifications

## disclaimer
have fun and dont steal my code please :(
