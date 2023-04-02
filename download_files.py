import itertools
import logging
import os
import requests
import sys
import time
import traceback
import uuid

from ast import literal_eval
from bs4 import BeautifulSoup as bs
from encryption_routine import encryption_routine
from headers import csrf_request_headers, download_image_headers

class Downloader:
    def __init__(self, record_URL, file_range, base_images_dir):
        self.session = requests.Session()
        self.record_URL = record_URL
        self.file_range = file_range
        self.base_images_dir = base_images_dir

        self.archive_directory_name = None
        self.image_URLs_and_labels = None
        self.csrf_token = None
        self.CRAWL_SPEED = 2 # 2 second delay between each archive request

    @classmethod
    def log_error_and_exit(cls, error_message):
        logging.error(error_message)
        sys.exit()

    @staticmethod
    def is_registers_url(url):
        try:
            response = requests.Session().get(url, headers=csrf_request_headers())
            soup = bs(response.text, "html.parser")
            register_header = soup.find("h3", {"id": "register-header"})
            return register_header is not None
        except Exception:
            return False

    def create_archive_directory(self):
        # archive_directory_path = f"{self.base_images_dir}\\{self.archive_directory_name}"
        archive_directory_path = os.path.join(self.base_images_dir,self.archive_directory_name)

        if not os.path.exists(self.base_images_dir):
            os.mkdir(self.base_images_dir)
            os.mkdir(archive_directory_path)
        if not os.path.exists(archive_directory_path):
            os.mkdir(archive_directory_path)

    def fetch_registers_page_and_download_all(self):
        try:
            # get first registers page to determine how many pages to parse
            logging.info(f"Download Register Page to enumumerate Registers: {self.record_URL}")
            response = self.session.get(self.record_URL, headers=csrf_request_headers())
            if response.status_code != 200:
                self.log_error_and_exit(f"{response.status_code} Status Code Fetching CSRF Token, Exiting")
                return
            registers_pages = list(self.registers_page_parse_list_pages(response.text))

            # get registers page to determine how many pages to parse
            def record_urls_from_page(base_url, page):
                url = f"{base_url}?page={page}"
                logging.info(f"Download Register Page to enumumerate Record URLs: {url}")
                response = self.session.get(url, headers=csrf_request_headers())
                if response.status_code != 200:
                    self.log_error_and_exit(f"{response.status_code} Status Code Fetching CSRF Token, Exiting")
                    return
                yield from self.registers_page_parse(response.text)

            logging.info(f"Found {registers_pages[-1]} Register Page(s).")
            record_urls_gens = (record_urls_from_page(self.record_URL, page) for page in registers_pages)
            record_urls = list(itertools.chain.from_iterable(record_urls_gens))
            logging.debug(f"Done emulerating all Record URLs:\n" + "\n".join(record_urls))

            # download all records
            for record_URL in record_urls:
                self.record_URL = record_URL
                self.fetch_record_page()
                self.download_files()
        except Exception:
            self.log_error_and_exit(f"Unexpected Error Fetching Record Page, Exiting\n{traceback.format_exc()}")

    def fetch_record_page(self):
        try:
            response = self.session.get(self.record_URL, headers=csrf_request_headers())
            if response.status_code == 200:
                self.get_csrf_token()
                self.record_page_parse_image_URLs_and_labels(response.text)
                self.record_page_parse_archive_name(response.text)
                self.create_archive_directory()
            else:
                self.log_error_and_exit(f"{response.status_code} Status Code Fetching CSRF Token, Exiting")
        except Exception:
            self.log_error_and_exit(f"Unexpected Error Fetching Record Page, Exiting\n{traceback.format_exc()}")

    def get_csrf_token(self):
        csrf_token = self.session.cookies.get_dict().get("shared_csrftoken")

        if csrf_token is not None:
            logging.info("Fetched CSRF")
            self.csrf_token = csrf_token
        else:
            self.log_error_and_exit("CSRF Token Not Received, Exiting")

    def registers_page_parse_list_pages(self, response_text):
        try:
            soup = bs(response_text, "html.parser")

            # there might be multiple pages
            register_header = soup.find("h3", {"id": "register-header"})
            page_links_list = register_header.findNext('ul')
            if page_links_list is None:
                yield 1
                return
            last_page_link = page_links_list.findAll("a", {"class": "page-link"})[-2]
            last_page_no = int(last_page_link.getText())
            pages = (i + 1 for i in range(last_page_no))

            yield from pages
        except Exception:
            self.log_error_and_exit(f"Unexpected Error Fetching Registers Page, Exiting\n{traceback.format_exc()}")

    def registers_page_parse(self, response_text):
        try:
            soup = bs(response_text, "html.parser")
            registers_table_rows = soup.find("div", {"class": "table-responsive"}).findAll("tr")
            registers_table_cells = (tr.findChildren("td") for tr in registers_table_rows)
            registers_name_cells = (tds[1] for tds in registers_table_cells if len(tds) > 1)
            registers_names = (td.getText() for td in registers_name_cells)
            record_urls = (f"{self.record_URL}/{register_name}" for register_name in registers_names)

            yield from record_urls
        except Exception:
            self.log_error_and_exit(f"Unexpected Error Fetching Registers Page, Exiting\n{traceback.format_exc()}")
            return []

    def record_page_parse_image_URLs_and_labels(self, record_reponse_text):
        if '"files":' not in record_reponse_text or "labels" not in record_reponse_text:
            self.log_error_and_exit("Error Parsing Image URLs, Exiting")

        start_files_list = record_reponse_text.split('"files":')[1]
        full_files_list = literal_eval(start_files_list.split('"],')[0].strip() + '"]')

        start_labels_list = record_reponse_text.split('"labels":')[1]
        full_labels_list = literal_eval(start_labels_list.split('"],')[0].strip() + '"]')

        self.image_URLs_and_labels = tuple(zip(full_files_list, full_labels_list))

        # if a range is provided and the range is less than the number of scraped files, use it
        if self.file_range is not None and self.file_range < len(self.image_URLs_and_labels):
            self.image_URLs_and_labels = self.image_URLs_and_labels[0:self.file_range]

        logging.info(f"Fetched List of {len(self.image_URLs_and_labels)} Images From '{self.record_URL}'")

    def record_page_parse_archive_name(self, record_reponse_text):
        try:
            soup = bs(record_reponse_text, "html.parser")
            archive_category = soup.find("table", {"class": "table table-register-data"}).find("a").text.strip()
            archive_category = "".join([char for char in archive_category if char.isalnum()]) # removing characters that could cause errors when writing creating dir
            archive_id = record_reponse_text.split("Archival identifier<td>")[1].split("\n")[0].strip()
            archive_id = "".join([char for char in archive_id if char.isalnum()])

            self.archive_directory_name = archive_category + "_" + archive_id
        except Exception:
            print("Error Parsing Archive Category and ID, Creating Random Directory Name")
            self.archive_directory_name = str(uuid.uuid4())[0:18] # first 18 chars of a random UUID

    def save_image(self, image_content, image_label, file_number):
        logging.info(f"Downloaded File {file_number} of {len(self.image_URLs_and_labels)}")
        file_path = f"{self.base_images_dir}\\{self.archive_directory_name}\\{image_label}.jpg"
        file_path = os.path.join(self.base_images_dir,self.archive_directory_name,f"{image_label}.jpg")

        with open(file_path, "wb") as f:
            f.write(image_content)

    def download_files(self):
        for index, (image_path, image_label) in enumerate(self.image_URLs_and_labels):
            request_attempts = 0
            url = encryption_routine.createValidURL(image_path, self.csrf_token)
            file_number = index + 1

            while request_attempts < 3:
                try:
                    response = self.session.get(url, headers=download_image_headers())
                    if response.status_code == 200:
                        self.save_image(response.content, image_label, file_number)
                    else:
                        logging.info(f"Skipping File {file_number} ({response.status_code} Response)")
                    time.sleep(self.CRAWL_SPEED)
                    break
                except requests.exceptions.ConnectionError:
                    if request_attempts < 3:
                        request_attempts += 1
                        logging.info(f"Retrying File {file_number}, Attempt ({request_attempts})")
                    else:
                        logging.info(f"Skipping File {file_number}, {request_attempts} failed attempts")
                except Exception:
                    logging.info(f"Skipping File {file_number}\n ({traceback.format_exc()})")
                    break


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    download = Downloader("https://data.matricula-online.eu/en/deutschland/akmb/militaerkirchenbuecher/0002/?pg=1", "./images")
    download.fetch_record_page()
    download.download_files()
