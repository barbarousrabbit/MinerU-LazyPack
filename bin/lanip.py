import socket

ips = set()
try:
    for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
        ips.add(ip)
except Exception:
    pass
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("10.255.255.255", 1))
    ips.add(s.getsockname()[0])
    s.close()
except Exception:
    pass
ips = sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))
for ip in ips:
    print("      http://%s:7860" % ip)
if not ips:
    print("      (no LAN address found, check the network connection)")
