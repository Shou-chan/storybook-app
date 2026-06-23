import streamlit as st
from openai import OpenAI
import requests
import tempfile
import os
import re
import json
import base64
from fpdf import FPDF

# --- PASSWORD PROTECTION ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Protected Storybook Builder")
    pwd = st.text_input("Enter Access Password", type="password")
    if st.button("Unlock App"):
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect Password! Access Denied.")
    st.stop()  # This entirely hides the rest of your app until unlocked
# ---------------------------

# Set up the page layout (The rest of your code continues down here...)
st.set_page_config(page_title="AI Storybook Builder", layout="wide")
st.title("📖 Autonomous Storybook Builder")

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

if 'book_pages' not in st.session_state:
    st.session_state.book_pages = {}

def parse_story_input(raw_text):
    pages = {}
    raw_pages = re.split(r'Page (\d+)', raw_text)
    for i in range(1, len(raw_pages), 2):
        page_num = int(raw_pages[i])
        content = raw_pages[i+1].strip()
        text_match = re.search(r'Text:(.*?)(?:Prompt:|$)', content, re.DOTALL)
        prompt_match = re.search(r'Prompt:(.*?)$', content, re.DOTALL)
        
        pages[page_num] = {
            "text": text_match.group(1).strip() if text_match else "",
            "prompt": prompt_match.group(1).strip() if prompt_match else "",
            "image_url": None,
            "qa_score": 0,
            "qa_feedback": "Not generated yet."
        }
    return pages

def perform_qa_check(image_url, text, prompt):
    # SAFETY CHECK: If no image was generated, skip the API call completely
    if not image_url:
        return 0, "Skipped QA: No image was generated due to a previous error."

    qa_system_prompt = f"""
    You are a strict Children's Book Art Director. 
    Review the image against this story text: "{text}" and prompt: "{prompt}"
    Evaluate exactly:
    1. Characters present?
    2. Setting accurate?
    3. Action correct?
    4. Facial Expressions match tone?
    Output JSON ONLY: {{"score": <number 1-10>, "feedback": "<Brief explanation>"}}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": qa_system_prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]}
            ],
            response_format={ "type": "json_object" }
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("score", 0), result.get("feedback", "QA Failed.")
    except Exception as e:
        # Changed default score to 0 so the app circuit-breaker knows to halt execution
        return 0, f"QA Error: {str(e)}"

def generate_page_image(page_num, prompt, text, retries=1):
    for attempt in range(retries + 1):
        with st.spinner(f"Generating Art for Page {page_num} (Attempt {attempt + 1})..."):
            try:
                response = client.images.generate(
                    model="gpt-image-2",
                    prompt=prompt + " 1990s classic 2D animated feature film style, nostalgic fairy tale cel-shading, vibrant flat colors. The main characters are young children. Keep character designs highly consistent.",
                    size="1024x1024",
                    quality="auto",
                    n=1,
                    # NO response_format parameter!
                )
                
                # Extract the native Base64 data your model provides
                raw_b64 = response.data[0].b64_json
                image_uri = f"data:image/png;base64,{raw_b64}"
                
                st.info(f"Running QA Check on Page {page_num}...")
                score, feedback = perform_qa_check(image_uri, text, prompt)
                
                if score >= 7 or attempt == retries:
                    st.session_state.book_pages[page_num]['image_url'] = image_uri
                    st.session_state.book_pages[page_num]['qa_score'] = score
                    st.session_state.book_pages[page_num]['qa_feedback'] = feedback
                    return True
                else:
                    st.warning(f"Page {page_num} failed QA (Score: {score}). Retrying...")
            except Exception as e:
                st.session_state.book_pages[page_num]['qa_feedback'] = f"SYSTEM ERROR: {str(e)}"
                return None

def create_storybook_pdf():
    # 210x373mm creates a perfect 9:16 smartphone aspect ratio
    pdf = FPDF(orientation="P", unit="mm", format=(210, 373))
    pdf.set_auto_page_break(auto=False)
    
    img_size = 261
    x_offset = -(img_size - 210) / 2
    
    for page_num in sorted(st.session_state.book_pages.keys()):
        data = st.session_state.book_pages[page_num]
        if data['image_url']:
            pdf.add_page()
            
            # --- DECODE BASE64 FOR PDF ---
            raw_b64_string = data['image_url'].split(",")[1]
            img_data = base64.b64decode(raw_b64_string)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
                temp_file.write(img_data)
                temp_path = temp_file.name
            
            pdf.image(temp_path, x=x_offset, y=0, w=img_size, h=img_size)
            os.remove(temp_path)
            
            pdf.set_xy(10, 275) 
            pdf.set_font("Helvetica", size=22) 
            clean_text = data['text'].encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(190, 10, txt=clean_text, align="C")
            
    return bytes(pdf.output())

# --- Main UI ---
st.subheader("1. Paste Your Story")
raw_story = st.text_area("Paste your fully formatted story here (Page X / Text: / Prompt:)", height=200)

if st.button("🚀 Generate Entire Book"):
    st.session_state.book_pages = parse_story_input(raw_story)
    
    # Create a dedicated space on the screen for live updates
    st.markdown("### 🎨 Live Studio Preview")
    preview_container = st.container()
    
    for page_num, data in st.session_state.book_pages.items():
        
        # 1. Generate the image
        image_result = generate_page_image(page_num, data['prompt'], data['text'])
        
        if image_result is None or image_result == False:
            exact_error = st.session_state.book_pages[page_num].get('qa_feedback', 'Unknown Error')
            st.error(f"🛑 Generation halted on Page {page_num}.\n\n**Exact Error:** {exact_error}")
            st.stop()
            
        # 2. --- NEW: LIVE PREVIEW DECODER ---
        # As soon as the page succeeds, decode it and draw it on the screen immediately!
        with preview_container:
            # Decode the base64 string
            raw_b64_string = st.session_state.book_pages[page_num]['image_url'].split(",")[1]
            image_bytes = base64.b64decode(raw_b64_string)
            
            # Display it beautifully in two columns
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(image_bytes, use_container_width=True)
            with col2:
                st.success(f"**Page {page_num} Complete!**")
                st.markdown(f"*{data['text']}*")
            st.markdown("---")
            
    # Once the loop finishes all pages, refresh the app to load the final dashboard
    st.rerun()

# Dashboard & Export
if st.session_state.book_pages:
    st.markdown("---")
    st.subheader("2. Review and Export")
    
    ready_pages = sum(1 for p in st.session_state.book_pages.values() if p['image_url'])
    if ready_pages > 0:
        pdf_bytes = create_storybook_pdf()
        st.download_button(
            label="📥 Download Storybook PDF",
            data=pdf_bytes,
            file_name="Storybook.pdf",
            mime="application/pdf",
            type="primary"
        )
    st.markdown("---")
    
    for page_num, data in sorted(st.session_state.book_pages.items()):
        with st.container():
            col1, col2 = st.columns([1, 1])
            with col1:
                if data['image_url']:
                    # --- DECODE BASE64 FOR DASHBOARD ---
                    raw_b64_string = data['image_url'].split(",")[1]
                    image_bytes = base64.b64decode(raw_b64_string)
                    st.image(image_bytes, use_container_width=True)
            with col2:
                st.markdown(f"### Page {page_num}")
                st.markdown(f"**Text:** {data['text']}")
                st.markdown(f"**QA Score:** {data['qa_score']}/10")
                st.markdown(f"**QA Feedback:** *{data['qa_feedback']}*")
                
                if st.button(f"🔄 Regenerate Page {page_num}", key=f"regen_{page_num}"):
                    generate_page_image(page_num, data['prompt'], data['text'])
                    st.rerun()
        st.markdown("---")
