from flask import Flask, request, jsonify
import pdfplumber
import base64
import os
import uuid
import tempfile

app = Flask(__name__)

# Fungsi untuk membersihkan titik pemisah ribuan agar menjadi angka murni
def clean_number(text):
    if not text: return 0
    text = str(text).strip()
    # Pisahkan angka desimal sen di belakang koma jika ada
    if ',' in text:
        text = text.split(',')[0]
    text = text.replace('.', '').strip()
    try:
        return int(text)
    except:
        return 0

@app.route('/api/extract', methods=['POST'])
def extract_pdf():
    try:
        data = request.get_json()
        if not data or 'pdf_base64' not in data:
            return jsonify({"error": "Tidak ada data pdf_base64"}), 400

        # Menerima file PDF dari Google Apps Script dan menyimpannya sementara
        pdf_data = base64.b64decode(data['pdf_base64'])
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"bku_{uuid.uuid4().hex}.pdf")
        
        with open(temp_path, "wb") as f:
            f.write(pdf_data)

        grouped_data = {} # Menggunakan dictionary untuk menggabungkan No. Bukti yang sama
        last_bukti_per_keg = {} # Mencatat No. Bukti terakhir berdasarkan Kode Kegiatan

        with pdfplumber.open(temp_path) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                
                for row in table:
                    if len(row) < 7: continue # Lewati jika bukan format tabel yang benar
                    
                    tanggal = str(row[0] or "").strip().replace('\n', ' ')
                    kode_keg = str(row[1] or "").strip().replace('\n', ' ')
                    kode_rek = str(row[2] or "").strip().replace('\n', ' ')
                    no_bukti = str(row[3] or "").strip().replace('\n', ' ')
                    uraian = str(row[4] or "").strip().replace('\n', ' ')
                    pengeluaran_str = str(row[6] or "0").strip()

                    if no_bukti == "4" and uraian == "5": continue

                    if not uraian or uraian.lower() == "uraian": continue

                    uraian_lower = uraian.lower()
                    # 1. ATURAN ABAIKAN: Saldo dan Terima
                    if "saldo bank" in uraian_lower or "saldo tunai" in uraian_lower:
                        continue
                    if "terima pph" in uraian_lower or "terima ppn" in uraian_lower:
                        continue

                    # 2. ATURAN PAJAK: Masukkan ke transaksi terakhir berdasarkan Kode Kegiatan
                    if "setor ppn" in uraian_lower:
                        last_bkt = last_bukti_per_keg.get(kode_keg)
                        if last_bkt and last_bkt in grouped_data:
                            grouped_data[last_bkt]["Nominal_PPN"] += clean_number(pengeluaran_str)
                        continue

                    elif "pph 22" in uraian_lower or "pph pasal 22" in uraian_lower:
                        last_bkt = last_bukti_per_keg.get(kode_keg)
                        if last_bkt and last_bkt in grouped_data:
                            grouped_data[last_bkt]["Nominal_PPh22"] += clean_number(pengeluaran_str)
                        continue
                        
                    elif "pph 23" in uraian_lower or "pph pasal 23" in uraian_lower:
                        last_bkt = last_bukti_per_keg.get(kode_keg)
                        if last_bkt and last_bkt in grouped_data:
                            grouped_data[last_bkt]["Nominal_PPh23"] += clean_number(pengeluaran_str)
                        continue
                        
                    elif "pajak daerah" in uraian_lower or "sspd" in uraian_lower or "pajak restoran" in uraian_lower or "pb1" in uraian_lower:
                        last_bkt = last_bukti_per_keg.get(kode_keg)
                        if last_bkt and last_bkt in grouped_data:
                            grouped_data[last_bkt]["Nominal_SSPD"] += clean_number(pengeluaran_str)
                        continue

                    elif "setor pph" in uraian_lower: # Default tangkap PPh 21
                        last_bkt = last_bukti_per_keg.get(kode_keg)
                        if last_bkt and last_bkt in grouped_data:
                            grouped_data[last_bkt]["Nominal_PPh21"] += clean_number(pengeluaran_str)
                        continue

                   # 3. ATURAN TRANSAKSI UTAMA: Ekstrak dan gabungkan data ganda
                    if no_bukti:
                        nominal = clean_number(pengeluaran_str)
                        detail_item = {"uraian": uraian, "nominal": nominal}
                        
                        # Catat No_Bukti ini sebagai transaksi terbaru untuk kode_keg ini
                        if kode_keg:
                            last_bukti_per_keg[kode_keg] = no_bukti

                        if no_bukti in grouped_data:
                            grouped_data[no_bukti]["Nominal_Pengeluaran"] += nominal
                            
                            if uraian not in grouped_data[no_bukti]["Uraian_BKU"]:
                                grouped_data[no_bukti]["Uraian_BKU"] += " | " + uraian
                            
                            grouped_data[no_bukti]["Detail_Belanja"].append(detail_item)
                        else:
                            grouped_data[no_bukti] = {
                                "Tanggal_Penerimaan": tanggal,
                                "Kode_Kegiatan": kode_keg,
                                "Kode_Rekening": kode_rek,
                                "No_Bukti": no_bukti,
                                "Uraian_BKU": uraian,
                                "Nominal_Pengeluaran": nominal,
                                "Nominal_PPh21": 0,
                                "Nominal_PPN": 0,
                                "Nominal_PPh23": 0,
                                "Nominal_SSPD": 0,
                                "Nominal_PPh22": 0,
                                "Detail_Belanja": [detail_item]
                            }

        # Ubah dictionary grouped_data kembali menjadi list agar sesuai format JSON awal
        extracted_data = list(grouped_data.values())
        return jsonify({"status": "success", "data": extracted_data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

if __name__ == '__main__':
    app.run(debug=True)
# redeploy
# force redeploy v2
