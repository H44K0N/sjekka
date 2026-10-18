# One-off experiment comparing Tesseract preprocessing on a receipt photo.
# Needs `pip install pytesseract` and the tesseract-ocr-nor language pack.
import pytesseract
from PIL import Image, ImageFilter, ImageEnhance
import sys

img = Image.open(sys.argv[1])

# Try 1 — greyscale
grey = img.convert('L')
print("=== GREYSCALE ===")
print(pytesseract.image_to_string(grey, lang='nor'))

# Try 2 — greyscale + sharpen
sharp = grey.filter(ImageFilter.SHARPEN)
print("=== SHARPEN ===")
print(pytesseract.image_to_string(sharp, lang='nor'))

# Try 3 — increase contrast
contrast = ImageEnhance.Contrast(grey).enhance(2.0)
print("=== CONTRAST ===")
print(pytesseract.image_to_string(contrast, lang='nor'))

# Try 4 — tesseract PSM 6 (assumes single block of text)
print("=== PSM 6 ===")
print(pytesseract.image_to_string(grey, lang='nor', config='--psm 6'))

# Try 5 — PSM 4 (single column)
print("=== PSM 4 ===")
print(pytesseract.image_to_string(grey, lang='nor', config='--psm 4'))
