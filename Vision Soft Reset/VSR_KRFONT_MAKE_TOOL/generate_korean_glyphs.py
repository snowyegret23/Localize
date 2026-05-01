import os
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "neodgm.ttf"
FONT_SIZE = 16
IMAGE_WIDTH = 18
IMAGE_HEIGHT = 18
OUTPUT_DIR = "HangulGlyph"
CHAR_LIST_PATH = "CharList_3864.txt"

def is_ascii_char(char_code):
    is_ascii = 32 <= char_code <= 127
    is_extended = 160 <= char_code <= 191
    return is_ascii or is_extended

def main():
    if not os.path.exists(FONT_PATH):
        print(f"Error: Font file not found at '{FONT_PATH}'")
        return
    if not os.path.exists(CHAR_LIST_PATH):
        print(f"Error: Character list file not found at '{CHAR_LIST_PATH}'")
        return
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        print(f"Created output directory: {OUTPUT_DIR}")

    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        with open(CHAR_LIST_PATH, 'r', encoding='utf-8') as f:
            text = f.read()
        chars_to_generate = sorted(list(set(text)))
    except Exception as e:
        print(f"An error occurred: {e}")
        return

    baseline_y = IMAGE_HEIGHT - 4
    print(f"Found {len(chars_to_generate)} unique characters in the list.")
    print("Generating glyphs for non-asvbii characters only...")

    count = 0
    skipped_count = 0
    for char in chars_to_generate:
        char_code = ord(char)

        if is_ascii_char(char_code):
            skipped_count += 1
            continue

        if char.isspace() and char != ' ':
            continue
        
        image = Image.new("RGBA", (IMAGE_WIDTH, IMAGE_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        try:
            bbox = draw.textbbox((0, 0), char, font=font, anchor='lt')
            text_width = bbox[2] - bbox[0]
        except AttributeError:
            text_width, _ = draw.textsize(char, font=font)

        x_pos = (IMAGE_WIDTH - text_width) / 2

        draw.text(
            (x_pos, baseline_y), 
            char, 
            font=font, 
            fill=(255, 255, 255, 255), 
            anchor='ls'
        )

        file_path = os.path.join(OUTPUT_DIR, f"{char_code}.png")
        image.save(file_path)
        
        count += 1
        if count % 100 == 0:
            print(f"generated {count} glyphs (skipped {skipped_count})")

    print("\nGlyph generation complete!")

if __name__ == "__main__":
    main()