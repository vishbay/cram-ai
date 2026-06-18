# vhs tape for the cram rig referee demo.
# Regenerate the GIF with:  vhs scripts/demo/referee.tape
# Requires: vhs (https://github.com/charmbracelet/vhs) + the cram-ai checkout.

Output docs/img/referee-demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1400
Set Height 620
Set Padding 24
Set Theme "Catppuccin Mocha"
Set PlaybackSpeed 0.6

Type "# cram rig is the referee: tokens at fixed success."
Enter
Sleep 1500ms
Type "# An 'optimization' that saves tokens by failing the task is not a win."
Enter
Sleep 1500ms
Type "python scripts/demo/referee_demo.py"
Sleep 800ms
Enter
Sleep 5s
