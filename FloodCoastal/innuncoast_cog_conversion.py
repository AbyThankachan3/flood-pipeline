import os
import subprocess

# -----------------------
# CONFIG
# -----------------------
INPUT_ROOT = "/home/labadmin/flood/inuncoast"
OUTPUT_ROOT = "/home/labadmin/flood/inuncoast_cog"

# GDAL COG options
GDAL_OPTIONS = [
    "-of", "COG",
    "-co", "COMPRESS=DEFLATE",
    "-co", "BLOCKSIZE=512",
    "-co", "BIGTIFF=YES",
    "-co", "NUM_THREADS=ALL_CPUS",
    "-co", "OVERVIEWS=IGNORE_EXISTING"
]


# -----------------------
# CONVERT FUNCTION
# -----------------------
def convert_to_cog(input_path, output_path):

    cmd = ["gdal_translate"] + GDAL_OPTIONS + [input_path, output_path]

    try:
        subprocess.run(cmd, check=True)
        print(f"   ✅ Converted: {os.path.basename(output_path)}")

    except subprocess.CalledProcessError:
        print(f"   ❌ Failed: {input_path}")


# -----------------------
# MAIN
# -----------------------
def main():

    total = 0
    skipped = 0

    for root, dirs, files in os.walk(INPUT_ROOT):

        for file in files:

            if not file.endswith(".tif"):
                continue

            input_path = os.path.join(root, file)

            # preserve folder structure
            rel_path = os.path.relpath(root, INPUT_ROOT)
            out_dir = os.path.join(OUTPUT_ROOT, rel_path)
            os.makedirs(out_dir, exist_ok=True)

            # output filename
            output_file = file.replace(".tif", "_cog.tif")
            output_path = os.path.join(out_dir, output_file)

            if os.path.exists(output_path):
                print(f"✔ Skipping (exists): {output_file}")
                skipped += 1
                continue

            print(f"\n📂 Processing: {input_path}")

            convert_to_cog(input_path, output_path)

            total += 1

    print("\n🎉 DONE")
    print("Converted:", total)
    print("Skipped:", skipped)


if __name__ == "__main__":
    main()