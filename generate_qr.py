import qrcode

link = "https://your-link-here.com"

qr = qrcode.QRCode(
    version=1,
    box_size=10,
    border=4,
)

qr.add_data(link)
qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white")
img.save("project_qr.png")

print("QR Code Generated Successfully!")