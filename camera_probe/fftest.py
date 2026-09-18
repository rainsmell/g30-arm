import subprocess
FF=r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
ALT=r"@device_pnp_\?\usb#vid_3408&pid_c200&mi_00#6&bb45298&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
cmd=[FF,"-hide_banner","-f","dshow","-video_size","640x480","-framerate","30","-i","video="+ALT,"-frames:v","5","-y","frames/other_%03d.png"]
p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
print(p.stderr[-1500:])
