import os
import zipfile

def main():
    dialogflow_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dialogflow"))
    zip_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dialogflow_agent.zip"))
    
    print(f"Zipping from: {dialogflow_dir}")
    print(f"Creating zip at: {zip_path}")
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(dialogflow_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, dialogflow_dir)
                zipf.write(file_path, arcname)
                print(f"  Added: {arcname}")
    print("Done!")

if __name__ == "__main__":
    main()
