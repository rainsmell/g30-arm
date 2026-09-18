import subprocess
FF=r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
ALT=r"@device_pnp_\?\usb#vid_0c45&pid_636b&mi_00#6&315a9f63&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
for extra in (["-vcodec","mjpeg"],[]):
    cmd=[FF,"-hide_banner","-f","dshow","-video_size","640x480","-framerate","30"]+extra+["-i","video="+ALT,"-frames:v","10","-y","frames/ff2_%03d.png"]
    p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace")
    print("==== extra",extra)
    print(p.stderr[-900:])
