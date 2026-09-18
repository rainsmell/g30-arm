import subprocess

FFMPEG = r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
ALT = r"@device_pnp_\\?\usb#vid_0c45&pid_636b&mi_00#6&315a9f63&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"

cmd = [FFMPEG, "-hide_banner", "-f", "dshow", "-list_options", "true",
       "-i", "video=" + ALT]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
print(p.stdout)
print(p.stderr)
