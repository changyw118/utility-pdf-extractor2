import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime
import io

st.set_page_config(page_title="TNB Analytics Dashboard", layout="wide")

def extract_tnb_data(pdf_file):
    data_list = []
    
    # Reset file pointer to the beginning to prevent PdfminerException
    pdf_file.seek(0)
    
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                # 1. EXTRACT MONTH & YEAR
                # Targets 'Tempoh Bill' inside the blue box area
                date_match = re.search(r'Tempoh\s*Bill\s*:\s*(\d{2}\.\d{2}\.\d{4})', text)
                if not date_match:
                    continue
                
                raw_date = date_match.group(1)
                dt_obj = datetime.strptime(raw_date, "%d.%m.%Y")
                
                # 2. EXTRACT kWh (Last column of 'Kegunaan kWh' row)
                kwh_val = 0.0
                lines = text.split('\n')
                for line in lines:
                    if "Kegunaan kWh" in line:
                        # Extract all decimal numbers; the last one is the 'Jumlah'
                        nums = re.findall(r'[\d,]+\.\d{2}', line)
                        if nums:
                            kwh_val = float(nums[-1].replace(',', ''))

                # 3. EXTRACT RM (Figure beside 'Caj Semasa')
                # Look for 'Caj Semasa' followed by RM and the amount
                rm_val = 0.0
                rm_match = re.search(r'Caj\s*Semasa\s*(?::|RM)?\s*RM?\s*([\d,]+\.\d{2})', text, re.IGNORECASE)
                if rm_match:
                    rm_val = float(rm_match.group(1).replace(',', ''))

                if kwh_val > 0 or rm_val > 0:
                    data_list.append({
                        "Year": dt_obj.year,
                        "Month": dt_obj.strftime("%b"),
                        "Month_Num": dt_obj.month,
                        "kWh": kwh_val,
                        "RM": rm_val
                    })
    except Exception as e:
        st.error(f"Error processing file: {e}")
                
    return data_list

# --- STREAMLIT UI ---
st.title("⚡ TNB Multi-Year Data Extractor")
st.markdown("Upload your monthly bills to generate comparison tables for **Usage (kWh)** and **Cost (RM)**.")

uploaded_files = st.file_uploader("Upload TNB PDFs", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    for f in uploaded_files:
        extracted = extract_tnb_data(f)
        if extracted:
            all_data.extend(extracted)
    
    if all_data:
        df = pd.DataFrame(all_data).drop_duplicates(subset=['Year', 'Month'])
        month_order = ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        # --- TABLE 1: kWh ---
        st.subheader("Summary Comparison Electricity Usage (kWh)")
        kwh_pivot = df.pivot(index='Month', columns='Year', values='kWh').reindex(month_order)
        st.dataframe(kwh_pivot.style.format("{:,.2f} kWh", na_rep="-"), use_container_width=True)
        
        # --- TABLE 2: RM ---
        st.subheader("Summary Comparison Electricity Cost (RM)")
        rm_pivot = df.pivot(index='Month', columns='Year', values='RM').reindex(month_order)
        st.dataframe(rm_pivot.style.format("RM {:,.2f}", na_rep="-"), use_container_width=True)
        
        # --- DOWNLOAD REPORT ---
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            kwh_pivot.to_excel(writer, sheet_name='kWh_Usage')
            rm_pivot.to_excel(writer, sheet_name='RM_Cost')
        
        st.download_button("📥 Download Excel Report", output.getvalue(), "TNB_Summary.xlsx")
    else:
        st.warning("No data found. Please ensure the PDFs are digital text-based bills.")
