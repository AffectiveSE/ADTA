import urllib.request

from config import PathConfig
from tqdm import tqdm

if __name__ == '__main__':
    with open(PathConfig.VIDEOS_LINKS) as f:
        links = f.readlines()

    for link in tqdm(links):
        filename = link.split('/')[-1].strip()
        urllib.request.urlretrieve(link, f"{PathConfig.VIDEOS_PATH}/{filename}")
