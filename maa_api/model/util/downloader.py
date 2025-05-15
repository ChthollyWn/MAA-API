import os
import shutil
import requests
import time

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from tqdm import tqdm

from maa_api.config.config import TEMP_PATH
from maa_api.log import logger
from maa_api.model.util.utils import HttpUtils

# 获取文件大小
def length(url_list):
    def getlenhead(single_url):
        response = HttpUtils.head(single_url, allow_redirects=True)
        file_size = response.headers.get('Content-Length')
        if file_size is not None:
            return int(file_size)
        else:
            return False

    for url in url_list:
        single_file_length = getlenhead(url)
        if single_file_length:
            return single_file_length, url
    
    raise RuntimeError("文件大小获取失败，地址: " + str(url_list))

# 定义Download类在初始化时保存几个参数
class Downloader:
    # 初始化类
    def __init__(self, urlist, chunksize, max_conn):
        self.urlist = urlist  # 镜像url列表
        self.chunksize = chunksize  # 分片大小
        self.max_conn = max_conn  # 单个url最大连接数
        self.lock = Lock()
        self.chunk_status = []  # 状态列表
        self.failed_requests = {url: {'success': 0, 'fail': 0} for url in urlist}  # 记录每个 URL 的失败次数和成功次数
        self.listhash = hex(hash(tuple(urlist)))  # 计算urlist的hash
        self.temp_path = TEMP_PATH / self.listhash  # 使用 Path 处理路径

    def download_chunk(self, url, chunk_id, total_size):
        start = chunk_id * self.chunksize
        end = min(start + self.chunksize - 1, total_size - 1)
        headers = {'Range': f'bytes={start}-{end}'}
        filename = self.temp_path / str(chunk_id)
        retries = 10  # 设置重试次数
        while retries > 0 and self.chunk_status[chunk_id] != 2:
            try:
                response = HttpUtils.get(url, headers=headers)
                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_url = response.headers['Location']
                    response = HttpUtils.get(redirect_url, headers=headers)

                if response.status_code == 206:
                    self.failed_requests[url]['success'] += 1
                    with open(filename, 'wb') as file:
                        file.write(response.content)
                    self.chunk_status[chunk_id] = 2
                    logger.info(f"分片 {chunk_id} 下载成功")
                    break
                elif response.status_code >= 400:
                    self.failed_requests[url]['fail'] += 1

            except requests.RequestException as e:
                if self.chunk_status[chunk_id] == 1:
                    self.chunk_status[chunk_id] = 0
            retries -= 1
            time.sleep(1)  # 等待一段时间后重试

        if retries == 0 and self.chunk_status[chunk_id] != 2:
            raise Exception(f"分片 {chunk_id} 下载失败，已达到最大重试次数")

    def download_file(self, total_size, file_path):
        num_chunks = (total_size + self.chunksize - 1) // self.chunksize
        self.chunk_status = [0] * num_chunks
        try:
            shutil.rmtree(self.temp_path)
        except FileNotFoundError:
            pass
        self.temp_path.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=self.max_conn * len(self.urlist)) as executor:
            for url in self.urlist:
                for chunk_id in range(num_chunks):
                    executor.submit(self.download_chunk, url, chunk_id, total_size)

        # 检查所有分片是否已成功下载
        if all(status == 2 for status in self.chunk_status):
            # 合并所有临时文件到一个文件
            with open(file_path, 'wb') as outfile:
                for chunk_id in range(num_chunks):
                    filename = self.temp_path / str(chunk_id)
                    with open(filename, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)

            # 删除临时目录
            shutil.rmtree(self.temp_path)
        else:
            logger.error("有分片下载失败，无法合并文件。")

        # 验证下载文件
        if os.path.getsize(file_path) != total_size:
            logger.warning("文件大小不一致，下载可能出错。")

    def download_file_no_chunk(self, download_url, file_path):
        try:
            response = HttpUtils.get(download_url, stream=True)
            response.raise_for_status()

            file_size = int(response.headers.get('Content-Length', 0))
            file_size_mb = file_size / (1024 * 1024)

            with tqdm(total=file_size, unit='B', unit_scale=True, desc=f"{file_path} ({file_size_mb:.2f} MB)", ncols=100) as progress_bar:
                with open(file_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                        progress_bar.update(len(chunk))

            logger.info(f"下载成功，已保存到 {file_path}")
        except requests.RequestException as e:
            logger.error(f"下载失败 : {e}")

def file_download(download_url_list, download_path):
    chunksize = 1024 * 1024     # 分片大小1MB
    max_conn = 4                # 最大连接数
    # 创建对象
    downloader = Downloader(download_url_list, chunksize, max_conn)
    # 下载文件
    total_size, download_url = length(download_url_list)
    logger.info(f"下载地址为{download_url}，文件大小为{total_size / (1024 * 1024)}MB，开始下载")
    return downloader.download_file_no_chunk(download_url, download_path)
