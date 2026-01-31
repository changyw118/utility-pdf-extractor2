import streamlit as st
import pandas as pd
import re
from datetime import datetime
import io
import gc 
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes

# --- Page Configuration ---
st.set_page_config(page_title="TNB Precise Industrial Extractor Pro", layout="wide", page_icon="⚡")

# --- Design Tokens (Aesthetics) ---
st.markdown("""
<style>
    .main {
        background-color: #f8f9fa;
    }
    .stMetric {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stTable {
        background-color: #ffffff;
        border-radius: 10px;
        overflow: hidden;
    }
    h1 {
        color: #1E3A8A;
        font-family: 'Inter', sans-serif;
        font-weight: 800;
    }
</style>
""", unsafe_allow_html=True)

def clean_industrial_num(raw_str):
    """Safely extracts the first valid number from a string."""
    if not raw_str: return 0.0
    # Find the first sequence of digits, commas, and dots that ends with two decimals
    match = re.search(r'[\d,.]*\d+\.\d{2}', raw_str)
    if not match:
        # Fallback for integers or other formats
        match = re.search(r'[\d,.]+', raw_str)
        
    if match:
        clean = "".join(c for c in match.group(0) if c.isdigit() or c == '.')
        if clean.count('.') > 1:
            parts = clean.split('.')
            clean = "".join(parts[:-1]) + "." + parts[-1]
        try:
            return float(clean)
        except:
            return 0.0
    return 0.0

def extract_data_from_text(text):
    """Core logic to extract Year, Month, kWh, and RM from raw text."""
    data = None
    
    # 1. Date Extraction (Billing Period Start Date)
    dt_obj = None
    
    # Method A: Look for "Tempoh Bil" line (Very reliable in digital)
    tempoh_match = re.search(r'Tempoh\s*Bil\s*:\s*.*?\s*(\d{2}[./-]\d{2}[./-]\d{4})', text, re.IGNORECASE)
    if tempoh_match:
        raw_date = tempoh_match.group(1).replace('-', '.').replace('/', '.')
        try:
            dt_obj = datetime.strptime(raw_date, "%d.%m.%Y")
        except: pass
    
    # Method B: Header Section (Original approach fallback)
    if not dt_obj:
        header_section = re.search(r'Tarikh\s*Bil(.*?)No\.\s*Invois', text, re.IGNORECASE | re.DOTALL)
        if header_section:
            dates = re.findall(r'(\d{2}[./-]\d{2}[./-]\d{4})', header_section.group(1))
            if len(dates) >= 2:
                # Often the second date is the end of the billing period, but sometimes it's the bill date.
                # Let's try to find a date that makes sense for the billing period.
                raw_date = dates[1].replace('-', '.').replace('/', '.')
                try:
                    dt_obj = datetime.strptime(raw_date, "%d.%m.%Y")
                except: pass
                
    # Method C: Any two dates (Final fallback)
    if not dt_obj:
        dates = re.findall(r'(\d{2}[./-]\d{2}[./-]\d{4})', text)
        if len(dates) >= 2:
            raw_date = dates[1].replace('-', '.').replace('/', '.')
            try:
                dt_obj = datetime.strptime(raw_date, "%d.%m.%Y")
            except: pass

    # 2. kWh & RM Extraction
    if dt_obj and 2010 <= dt_obj.year <= 2030:
        kwh_val = 0.0
        rm_val = 0.0

        # --- kWh Extraction ---
        # Pattern 1: New format "Jumlah Penggunaan Anda (1,036,378 kWh)"
        new_kwh_match = re.search(r'Jumlah\s*Penggunaan\s*Anda\s*\(([\d\s,.]+)\s*kWh\)', text, re.IGNORECASE)
        if new_kwh_match:
            kwh_val = clean_industrial_num(new_kwh_match.group(1))
        else:
            # Pattern 2: Old format "Kegunaan kWh"
            old_kwh_match = re.search(r'Kegunaan\s*(?:kWh|KWH|kVVh).*?([\d\s,.]+\d{2})', text, re.IGNORECASE | re.DOTALL)
            if old_kwh_match:
                kwh_val = clean_industrial_num(old_kwh_match.group(1))

        # --- RM Extraction ---
        # Pattern 1: New format "Caj Semasa RM 593,563.47" or "Caj Semasa RM593,563.47"
        # Note: We look for "Caj Semasa" followed by RM and a number.
        new_rm_match = re.search(r'Caj\s*Semasa\s*(?:RM)?\s*([\d\s,.]+\d{2})', text, re.IGNORECASE)
        if new_rm_match:
            rm_val = clean_industrial_num(new_rm_match.group(1))
        
        # Pattern 2: Old format "Jumlah Perlu Bayar"
        if rm_val == 0.0:
            old_rm_match = re.search(r'Jumlah\s*Perlu\s*Bayar.*?([\d\s,.]+\d{2})', text, re.IGNORECASE | re.DOTALL)
            if old_rm_match:
                rm_val = clean_industrial_num(old_rm_match.group(1))
            else:
                # Fallback for different layouts
                backup = list(re.finditer(r'(?:Jumlah|Total|Caj).*?([\d\s,.]+\d{2})', text, re.IGNORECASE | re.DOTALL))
                if backup:
                    rm_val = clean_industrial_num(backup[-1].group(1))

        if kwh_val > 0 or rm_val > 0:
            data = {
                "Year": dt_obj.year,
                "Month": dt_obj.strftime("%b"),
                "Month_Num": dt_obj.month,
                "kWh": kwh_val,
                "RM": rm_val,
                "Status": "Found"
            }
    return data

def process_pdf(pdf_file):
    """Processes PDF using pdfplumber (fast) with OCR fallback (steady)."""
    data_map = {} # (year, month_num) -> data_dict
    try:
        # Use a context manager for pdfplumber
        with pdfplumber.open(pdf_file) as pdf:
            total_pages = len(pdf.pages)
            progress_text = f"Processing {pdf_file.name}..."
            my_bar = st.progress(0, text=progress_text)
            
            for i, page in enumerate(pdf.pages):
                my_bar.progress((i + 1) / total_pages, text=f"{progress_text} (Page {i+1}/{total_pages})")
                
                # 1. Try Direct Text Extraction (Fast & Precise)
                text = page.extract_text()
                page_data = None
                
                if text and len(text.strip()) > 50:
                    page_data = extract_data_from_text(text)
                
                # 2. If Direct Extraction fails or yields nothing, try OCR (Fallback)
                if not page_data:
                    # Convert only this specific page to image to save memory
                    pdf_file.seek(0)
                    images = convert_from_bytes(pdf_file.read(), first_page=i+1, last_page=i+1, dpi=200, grayscale=True)
                    if images:
                        ocr_text = pytesseract.image_to_string(images[0], lang="eng", config='--psm 6')
                        page_data = extract_data_from_text(ocr_text)
                        images[0].close()
                        del images
                
                if page_data:
                    key = (page_data['Year'], page_data['Month_Num'])
                    if key not in data_map:
                        data_map[key] = page_data
                    else:
                        # Merge: prioritize non-zero kWh and RM if they exist
                        if page_data['kWh'] > 0:
                            data_map[key]['kWh'] = page_data['kWh']
                        if page_data['RM'] > 0:
                            data_map[key]['RM'] = page_data['RM']
                        data_map[key]['Status'] = "Found"
                
                # Clean up memory intermittently
                if i % 10 == 0:
                    gc.collect()
                    
            my_bar.empty()
            
    except Exception as e:
        st.error(f"⚠️ Error processing {pdf_file.name}: {e}")
    
    return list(data_map.values())

# --- UI Layout ---
st.title("⚡ TNB Industrial Smart Extractor Pro")
st.markdown("Automated utility data extraction for industrial bills. Now faster and more accurate.")

uploaded_files = st.file_uploader("📤 Upload TNB Industrial Bills (PDF)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    all_results = []
    
    with st.spinner("Analyzing files..."):
        for f in uploaded_files:
            data = process_pdf(f)
            if data: all_results.extend(data)
    
    if all_results:
        # Deduplicate and merge results from all files
        df_raw = pd.DataFrame(all_results)
        
        # Group by year and month to merge results from different files/pages
        df = df_raw.groupby(['Year', 'Month_Num']).agg({
            'Month': 'first',
            'kWh': 'max',
            'RM': 'max',
            'Status': 'first'
        }).reset_index()
        
        # --- Identify Missing Months ---
        df['date'] = pd.to_datetime(df.apply(lambda x: f"{int(x['Year'])}-{int(x['Month_Num'])}-01", axis=1))
        min_date = df['date'].min()
        max_date = df['date'].max()
        
        if not pd.isna(min_date) and not pd.isna(max_date):
            all_months = pd.date_range(start=min_date, end=max_date, freq='MS')
            full_df = pd.DataFrame({'date': all_months})
            df = pd.merge(full_df, df, on='date', how='left')
            
            # Fill missing attributes
            df['Year'] = df['date'].dt.year
            df['Month'] = df['date'].dt.strftime('%b')
            df['Month_Num'] = df['date'].dt.month
            df['kWh'] = df['kWh'].fillna(0.0)
            df['RM'] = df['RM'].fillna(0.0)
            df['Status'] = df['Status'].fillna('MISSING')
        
        df = df.sort_values(['Year', 'Month_Num']).drop(columns=['date'])
        
        st.divider()
        st.subheader("📊 Extracted Summary")
        
        # Display key metrics (only for Found data)
        found_df = df[df['Status'] == 'Found']
        total_kwh = found_df['kWh'].sum()
        total_rm = found_df['RM'].sum()
        avg_rm_kwh = total_rm / total_kwh if total_kwh > 0 else 0
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Total kWh", f"{total_kwh:,.2f}")
        c2.metric("Total RM", f"RM {total_rm:,.2f}")
        c3.metric("Avg RM/kWh", f"{avg_rm_kwh:.4f}")

        # Highlight missing months in Streamlit
        def highlight_missing(row):
            if row['Status'] == 'MISSING':
                return ['background-color: #ffebee; color: #c62828'] * len(row)
            return [''] * len(row)

        st.table(df[['Year', 'Month', 'kWh', 'RM', 'Status']].style.format({
            'kWh': "{:,.2f}", 
            'RM': "{:,.2f}"
        }).apply(highlight_missing, axis=1))
        
        # Register missing months in Streamlit warning
        missing_count = (df['Status'] == 'MISSING').sum()
        if missing_count > 0:
            missing_months_str = ", ".join(df[df['Status'] == 'MISSING'].apply(lambda x: f"{x['Month']} {x['Year']}", axis=1))
            st.warning(f"🚨 **{missing_count} Missing Months Detected:** {missing_months_str}")

        # Excel Export
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df[['Year', 'Month', 'kWh', 'RM', 'Status']].to_excel(writer, index=False, sheet_name='TNB_Data')
            workbook  = writer.book
            worksheet = writer.sheets['TNB_Data']
            
            # Formatting
            num_format = workbook.add_format({'num_format': '#,##0.00'})
            missing_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
            
            worksheet.set_column('C:D', 20, num_format)
            worksheet.set_column('E:E', 15)
            
            # Highlight MISSING rows in Excel
            for i, row in df.iterrows():
                if row['Status'] == 'MISSING':
                    worksheet.set_row(i + 1, None, missing_format)
            
        st.download_button(
            label="📥 Download Formatted Excel Report",
            data=output.getvalue(),
            file_name=f"TNB_Data_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("No valid TNB data patterns found in the uploaded files.")
else:
    st.info("💡 Tip: You can upload multiple files at once. The app will automatically merge and deduplicate the data.")

# --- Footer ---
st.caption("v2.0 - Optimized with pdfplumber & Intelligent Date Parsing")   please help me add in code that enable the excel report generated include one more column name Production data
