import os, logging, threading, string, json, time
from datetime import date, timedelta
from tesserocr import PyTessBaseAPI
from PIL import Image as PIL_Image

from flask import Flask, request, render_template, send_from_directory

FILE_SLEEP_INTERVAL = 1
LOOP_SLEEP_INTERVAL = 10

RESULTS_SHOWN = 12

# if the png file is already small (100KB or so, then lossy compression is sometimes bigger than lossless compression)
BIG_FILE_THRESHOLD = 1000000

most_recent = None
page_metadata = {}
cdom_inverted_index = {}
onscreen_inverted_index = {}
irchiver_folder = os.path.join(os.getenv('APPDATA'), 'irchiver')

logging.basicConfig(format='%(asctime)s - %(message)s', filename=os.path.join(irchiver_folder, 'server.log'), encoding='utf-8', level=logging.INFO)
logger = logging.getLogger()

def indexer():
	logging.info('indexer thread started')
	global irchiver_folder
	page_metadata_file = os.path.join(irchiver_folder, 'page_metadata.json')
	cdom_inverted_index_file = os.path.join(irchiver_folder, 'cdom_inverted_index.json')
	onscreen_inverted_index_file = os.path.join(irchiver_folder, 'onscreen_inverted_index.json')

	while True:
		logger.debug('[INDEXER] indexing .txt files')
		indexed_files = 0
		global page_metadata
		if os.path.exists(page_metadata_file):
			page_metadata = json.load(open(page_metadata_file, encoding='utf-8'))
		global cdom_inverted_index
		if os.path.exists(cdom_inverted_index_file):
			cdom_inverted_index = json.load(open(cdom_inverted_index_file, encoding='utf-8'))
		for filename in os.listdir(irchiver_folder):
			filepath = os.path.join(irchiver_folder, filename)
			if filename.endswith(".txt"):
				url = None
				screenshotid = None
				timestamp = None
				pageid = filename.rsplit('.', 1)[0]
				if pageid in page_metadata and page_metadata[pageid]['ocr']:
					# already loaded, and has been OCRed so the indexing must have finished
					continue
				indexed_files += 1
				for line_number, line in enumerate(open(filepath, encoding="utf-8", errors='ignore')):
					line = line.strip()
					if line_number == 0:
						url = line
					elif line_number == 1:
						screenshotid = line
					elif line_number == 2:
						timestamp = line
						#assert(pageid not in page_metadata) # no longer always true
						page_metadata[pageid] = {'url': url, 'screenshotid': screenshotid, 'timestamp': timestamp, 'title': None, 'ocr': False} # set it up with no title because the title is sometimes empty
					elif line_number == 3:
						title = line
						page_metadata[pageid]['title'] = title
					else:
						tokens = line.translate(str.maketrans('', '', string.punctuation)).lower().split()
						for token in tokens:
							if token not in cdom_inverted_index:
								cdom_inverted_index[token]  = {}
							if pageid not in cdom_inverted_index[token]:
								cdom_inverted_index[token][pageid] = 0
							cdom_inverted_index[token][pageid] += 1
				if screenshotid != pageid: # if there's nothing to OCR, then just mark it OCRed so it can be deleted later
					page_metadata[pageid]['ocr'] = True

		with open(cdom_inverted_index_file, 'w', encoding='utf-8') as f:
			json.dump(cdom_inverted_index, f, ensure_ascii=False, indent="\t")
		with open(page_metadata_file, 'w', encoding='utf-8') as f:
			json.dump(page_metadata, f, ensure_ascii=False, indent="\t")
		
		global most_recent
		if len(page_metadata) > 0:
			most_recent = page_metadata[max(page_metadata, key=lambda p: page_metadata[p]['timestamp'])]['timestamp']

		if indexed_files > 0:
			logger.info('[INDEXER] indexed this many new files: ' + str(indexed_files))

		logger.debug('[INDEXER] indexing (OCR) .png files')

		tesserapi = PyTessBaseAPI()

		ocred_files = 0
		global onscreen_inverted_index
		if os.path.exists(onscreen_inverted_index_file):
			onscreen_inverted_index = json.load(open(onscreen_inverted_index_file, encoding='utf-8'))
		for filename in os.listdir(irchiver_folder):
			filepath = os.path.join(irchiver_folder, filename)
			if filename.endswith(".png"):
				pageid = filename.rsplit('.', 1)[0]
				if pageid not in page_metadata or page_metadata[pageid]['ocr']:
					# either the page isn't indexed yet (so it must have appeared after the indexer was run) or it has already been indexed and OCRed
					continue
				ocred_files += 1
				tesserapi.SetImageFile(filepath)
				tokens = tesserapi.GetUTF8Text().translate(str.maketrans('', '', string.punctuation)).lower().split()
				for token in tokens:
					if token not in onscreen_inverted_index:
						onscreen_inverted_index[token]  = {}
					if pageid not in onscreen_inverted_index[token]:
						onscreen_inverted_index[token][pageid] = 0
					onscreen_inverted_index[token][pageid] += 1
				page_metadata[pageid]['ocr'] = True

				time.sleep(FILE_SLEEP_INTERVAL)

		with open(onscreen_inverted_index_file, 'w', encoding='utf-8') as f:
			json.dump(onscreen_inverted_index, f, ensure_ascii=False, indent="\t")
		with open(page_metadata_file, 'w', encoding='utf-8') as f:
			json.dump(page_metadata, f, ensure_ascii=False, indent="\t")

		if ocred_files > 0:
			logger.info('[INDEXER] OCRed this many new .png files: ' + str(ocred_files))

		logger.debug('[INDEXER] archiving (converting) .png files to smaller .webp files')

		converted_files = 0
		for filename in os.listdir(irchiver_folder):
			filepath = os.path.join(irchiver_folder, filename)
			if filename.endswith(".png"):
				pageid = filename.rsplit('.', 1)[0]
				webp_filepath = os.path.join(irchiver_folder, pageid) + '.webp'
				if os.path.exists(webp_filepath):
					# already converted
					continue
				converted_files += 1 
				png_file_size = os.path.getsize(filepath)
				is_big_file = png_file_size > BIG_FILE_THRESHOLD
				quality = 80 # the bigger, the harder it tries to compress for lossless or the bigger the file for lossy
				if is_big_file:
					quality = max(20, 100 - 5 * png_file_size / BIG_FILE_THRESHOLD)
				png_image = PIL_Image.open(filepath)
				png_image.save(webp_filepath, lossless = not is_big_file, quality = quality) # maybe tag it when it's lossy

				time.sleep(FILE_SLEEP_INTERVAL)

		if converted_files > 0:
			logger.info('[INDEXER] converted this many new .png files to .webp: ' + str(converted_files))

		logger.debug('[INDEXER] cleaning up (removing) files from before yesterday')

		removed_files = 0
		for filename in os.listdir(irchiver_folder):
			filepath = os.path.join(irchiver_folder, filename)
			if filename.endswith(".png") or filename.endswith(".txt"):
				pageid = filename.rsplit('.', 1)[0]
				yyyymmdd = filename.split('.', 1)[0]
				assert(yyyymmdd.isdigit())
				# can't remove today's files because irchiver.exe uses the filenames to increment and not collide pageids
				if date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8])) >= date.today():
					continue
				if pageid not in page_metadata or not page_metadata[pageid]['ocr']:
					continue
				# always remove .txt files if it's in page_metadata and has been OCRed (meaning the indexnig was successful)
				# but only remove .png files if the .webp exists as well, so it's been OCRed (to get in page_metadata) and been converted to .webp (though not sure if this check is necessary)
				if filename.endswith(".txt"):
					os.remove(filepath)
					removed_files += 1
				if filename.endswith(".png"):
					webp_filepath = os.path.join(irchiver_folder, pageid) + '.webp'
					if os.path.exists(webp_filepath):
						os.remove(filepath)
						removed_files += 1

		if removed_files > 0:
			logger.info('[INDEXER] removed this many old files: ' + str(removed_files))

		time.sleep(LOOP_SLEEP_INTERVAL)

thread = threading.Thread(target=indexer)
thread.daemon = True         # Daemonize 
thread.start()

app = Flask(__name__)
app.config.update(SESSION_COOKIE_SECURE=True)

@app.after_request
def prevent_cross_origins(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response

@app.route("/screenshots/<path:name>")
def download_file(name):
    return send_from_directory(irchiver_folder, name, mimetype='image/webp')

@app.route('/', methods = ['GET', 'POST'])
def index():
	logger.debug('received request')

	if request.method == 'POST':
		logger.debug('received POST with query: ' + request.form['query'])

		if request.form['query']:
			tokens = request.form['query'].translate(str.maketrans('', '', string.punctuation)).lower().split()
			cdom_results = None
			if 'page-source' in request.form:
				for token in tokens:
					if token in cdom_inverted_index:
						r = set(cdom_inverted_index[token])
						if cdom_results is None:
							cdom_results = r
						else:
							cdom_results &= r
					else:
						cdom_results = None
						break
			if cdom_results is None:
				cdom_results = set()

			onscreen_results = None
			if 'screenshot-ocr' in request.form:
				for token in tokens:
					if token in onscreen_inverted_index:
						r = set(onscreen_inverted_index[token])
						if onscreen_results is None:
							onscreen_results = r
						else:
							onscreen_results &= r
					else:
						onscreen_results = None
						break
			if onscreen_results is None:
				onscreen_results = set()

			results = []
			for result in cdom_results:
				results.append({
					'url': page_metadata[result]['url'],
					'timestamp': page_metadata[result]['timestamp'],
					'title': page_metadata[result]['title'],
					'screenshot': 'screenshots/' + page_metadata[result]['screenshotid'] + '.webp',
					'result_type': 2 if result in onscreen_results else 0 # 0 means cdom_result, 1 means onscreen_result, 2 means both
				})
			for result in onscreen_results:
				if result not in cdom_results:
					results.append({
						'url': page_metadata[result]['url'],
						'timestamp': page_metadata[result]['timestamp'],
						'title': page_metadata[result]['title'],
						'screenshot': 'screenshots/' + page_metadata[result]['screenshotid'] + '.webp',
						'result_type': 1 # 0 means cdom_result, 1 means onscreen_result, 2 means both
					})
			
			results.sort(key=lambda r: r['timestamp'], reverse=True)
			
			prevURL = None
			for result in results:
				if prevURL == result['url']:
					result['same_url'] = True
				prevURL = result['url']

			return render_template('index.html', results = results[:RESULTS_SHOWN], n_results = len(results), query = request.form['query'], screenshot_ocr_unchecked = 'screenshot-ocr' not in request.form, page_source_unchecked = 'page-source' not in request.form, n_indexed = len(page_metadata), most_recent = most_recent)

	return render_template('index.html', n_indexed = len(page_metadata), most_recent = most_recent)

if __name__ == "__main__":
	app.run(debug=False)
