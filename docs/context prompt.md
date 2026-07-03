# context prompt

Summarize this Python game development session and everything we've done so far into a clean snapshot.

Your summary must include:
1. The Game Loop State: The framework (e.g., Pygame, Ursina, Arcade), screen resolution, target FPS, and current engine setup.
2. The Core State Machine: Active game states (e.g., MENU, PLAYING, GAME_OVER) and global variables.
3. Assets & Paths: Active images, sounds, or map files currently mapped out.
4. Completed Features: What is working perfectly right now (e.g., player movement, collision detection).
5. The Current Bug/Task: The exact file, class, or method we are writing or debugging right now.

Format this as a clean markdown file and title it "context #(number of context files in docs file so far including this one)". Put it in the docs folder.
