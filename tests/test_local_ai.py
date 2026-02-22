import os
import sys
import shutil

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.router import TicketRouter
from database.connection import get_db

def run_test():
    print("🚀 Initializing FIRE Local AI Diagnostic...")
    router = TicketRouter(ai_mode="qwen")
    
    # Test case 1: Text only
    print("\n--- Test Case 1: Text Only ---")
    description = "Не могу зайти в личный кабинет, постоянно пишет 'Ошибка сервера'. Помогите!"
    result = router.route_single_ticket(description)
    print(f"Type: {result['ai_analysis']['type']}")
    print(f"Summary: {result['ai_analysis']['summary']}")
    
    # Test case 2: Text + Image (Real Image) ---
    print("\n--- Test Case 2: Text + Image (Real Image) ---")
    real_image = r"D:\Altay\hackathon\datasaur26\input\attachments\order_error.png"
    
    if not os.path.exists(real_image):
        print(f"❌ Error: Image not found at {real_image}")
        return
        
    print(f"✅ Using real image for test: {real_image}")
            
    description_with_img = "Я пытаюсь отправить ордер, но приложение выдает ошибку. Скриншот во вложении."
    
    # We'll mock the check for attachment for the router
    result_img = router.route_single_ticket(
        description_with_img, 
        attachment_path=os.path.abspath(real_image)
    )
    
    print(f"Type: {result_img['ai_analysis']['type']}")
    print(f"Instruction for VLM: {result_img['ai_analysis'].get('image_extraction_instruction')}")
    print(f"Final Summary (Merged): {result_img['ai_analysis']['summary']}")
    
    if result_img['flags'].get('vision_processing_failed'):
        print("⚠️ Vision processing failed (Model likely not pulled yet)")
    else:
        print("✅ Vision processing triggered successfully")

if __name__ == "__main__":
    run_test()
