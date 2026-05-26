from flask import Flask, request, jsonify
import pdfplumber
import base64
import os

app = Flask(__name__)

# Fungsi untuk membersihkan titik pemisah ribuan agar menjadi angka murni
def clean_number(text):
    if not text: return 0
    text = str(text).replace('.', '').replace(',', '').strip()
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
        temp_path = "/tmp/temp_bku.pdf" 
        
        with open(temp_path, "wb") as f:
            f.write(pdf_data)

        grouped_data = {} # Menggunakan dictionary untuk menggabungkan No. Bukti yang sama
        pending_taxes = {} # Kantong penyimpan pajak sementara

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

                    # 2. ATURAN PAJAK: Simpan ke kantong berdasarkan Kode Kegiatan
                    if "setor ppn" in uraian_lower:
                        if kode_keg not in pending_taxes:
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}
                        pending_taxes[kode_keg]['ppn'] += clean_number(pengeluaran_str)
                        continue
                        
                    elif "pph 23" in uraian_lower or "pph pasal 23" in uraian_lower:
                        if kode_keg not in pending_taxes:
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}
                        pending_taxes[kode_keg]['pph23'] += clean_number(pengeluaran_str)
                        continue
                        
                    elif "pajak daerah" in uraian_lower or "sspd" in uraian_lower or "pajak restoran" in uraian_lower or "pb1" in uraian_lower:
                        if kode_keg not in pending_taxes:
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}
                        pending_taxes[kode_keg]['sspd'] += clean_number(pengeluaran_str)
                        continue

                    elif "setor pph" in uraian_lower: # Default tangkap PPh 21
                        if kode_keg not in pending_taxes:
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}
                        pending_taxes[kode_keg]['pph21'] += clean_number(pengeluaran_str)
                        continue

                   # 3. ATURAN TRANSAKSI UTAMA: Ekstrak, tempelkan pajak, dan gabungkan data ganda
                    if no_bukti:
                        pph21 = 0; ppn = 0; pph23 = 0; sspd = 0
                        if kode_keg in pending_taxes:
                            pph21 = pending_taxes[kode_keg]['pph21']
                            ppn = pending_taxes[kode_keg]['ppn']
                            pph23 = pending_taxes[kode_keg]['pph23']
                            sspd = pending_taxes[kode_keg]['sspd']
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}

                        nominal = clean_number(pengeluaran_str)

                        # --- BARU: Rekam Rincian Asli untuk Bukti Penerimaan ---
                        detail_item = {"uraian": uraian, "nominal": nominal}

                        if no_bukti in grouped_data:
                            grouped_data[no_bukti]["Nominal_Pengeluaran"] += nominal
                            grouped_data[no_bukti]["Nominal_PPh21"] += pph21
                            grouped_data[no_bukti]["Nominal_PPN"] += ppn
                            grouped_data[no_bukti]["Nominal_PPh23"] += pph23
                            grouped_data[no_bukti]["Nominal_SSPD"] += sspd
                            
                            if uraian not in grouped_data[no_bukti]["Uraian_BKU"]:
                                grouped_data[no_bukti]["Uraian_BKU"] += " | " + uraian
                            
                            # Simpan ke memori rincian asli
                            grouped_data[no_bukti]["Detail_Belanja"].append(detail_item)
                        else:
                            grouped_data[no_bukti] = {
                                "Tanggal_Penerimaan": tanggal,
                                "Kode_Kegiatan": kode_keg,
                                "Kode_Rekening": kode_rek,
                                "No_Bukti": no_bukti,
                                "Uraian_BKU": uraian,
                                "Nominal_Pengeluaran": nominal,
                                "Nominal_PPh21": pph21,
                                "Nominal_PPN": ppn,
                                "Nominal_PPh23": pph23,
                                "Nominal_SSPD": sspd,
                                "Detail_Belanja": [detail_item] # Buat array baru
                            }

        # Ubah dictionary grouped_data kembali menjadi list agar sesuai format JSON awal
        extracted_data = list(grouped_data.values())

        # Hapus file sementara setelah selesai
        if os.path.exists(temp_path):
            os.remove(temp_path)

        return jsonify({"status": "success", "data": extracted_data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
