from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import requests
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
import json
import os
import time
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import redis
from abc import ABC, abstractmethod

# Settings
STATIC_AUTH_TOKEN = "my_secure_token"
CACHE_EXPIRY = 3600  # seconds

# Initialize FastAPI app
app = FastAPI()

# In-memory DB (Redis) setup
cache = redis.Redis(host='localhost', port=6379, decode_responses=True)

# Authentication dependency
def auth_dependency(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    if credentials.credentials != STATIC_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing authentication token")

# Pydantic models
class ScrapeSettings(BaseModel):
    max_pages: Optional[int] = Field(default=None, description="Max number of pages to scrape")
    proxy: Optional[str] = Field(default=None, description="Proxy string for scraping")

class ScrapedProduct(BaseModel):
    name: str
    price: str
    url: str

class StorageStrategy(ABC):
    @abstractmethod
    def save(self, data, filepath: str):
        pass

class FileStorageStrategy(StorageStrategy):
    def save(self, data, filepath: str):
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)

class NotificationStrategy(ABC):
    @abstractmethod
    def notify(self, message):
        pass

class ConsoleNotificationStrategy(NotificationStrategy):
    def notify(self, message):
        print(message)

class Scraper:
    def __init__(self, settings: ScrapeSettings, storage_strategy: StorageStrategy , notification_strategy: NotificationStrategy):
        self.settings = settings
        self.scraped_data: List[Dict[str, Any]] = []
        self.storage_strategy = storage_strategy
        self.notification_strategy = notification_strategy

    def scrape_page(self, url: str) -> List[ScrapedProduct]:
        page = requests.get(url)
        soup = BeautifulSoup(page.content, 'html.parser')

        products = []

        product_items = soup.find_all('li', class_='product-inner  clearfix')
        titles = soup.select('.mf-product-content .woo-loop-product__title')
        for title in titles:
            print(title.get_text(strip=True))
        
        prices = soup.select('.mf-product-price-box .woocommerce-Price-amount')
        for price in prices:
            price_text = price.get_text(strip=True)
            print(price_text)

        images = soup.select('.mf-product-thumbnail img')
        for img in images:
            img_url = img['src']
            print(img_url)

        for title, price, image in zip(titles, prices, images):
            title_text = title.get_text(strip=True)
            price_text = price.get_text(strip=True)
            image_url = image['src']

            product = ScrapedProduct(
                name=title_text,
                price=price_text,
                url=image_url
            )

            products.append(product)
        
        return products

    def save_image(self, img_url: str) -> str:
        try:
            response = requests.get(img_url, stream=True)
            response.raise_for_status()
            img_name = img_url.split("/")[-1]
            img_path = os.path.join("images", img_name)
            os.makedirs("images", exist_ok=True)
            with open(img_path, "wb") as img_file:
                for chunk in response.iter_content(1024):
                    img_file.write(chunk)
            return img_path
        except RequestException as e:
            print(f"Error downloading image {img_url}: {e}")
            return ""

    def scrape_catalogue(self, base_url: str):
        page = 1
        while page<10:
            if self.settings.max_pages and page > self.settings.max_pages:
                break

            url = f"{base_url}?page={page}"
            print(f"Scraping page: {page}")

            # Retry mechanism
            retries = 3
            while retries > 0:
                products = self.scrape_page(url)
                if products:
                    break
                retries -= 1
                time.sleep(2)

            for product in products:
                cache_key = f"product:{product.name}"
                cached_price = cache.get(cache_key)

                if cached_price is None or cached_price != product.price:
                    self.scraped_data.append(product.dict())
                    cache.set(cache_key, product.price, ex=CACHE_EXPIRY)

            if not products:
                break

            page += 1




@app.post("/scrape")
def start_scraping(settings: ScrapeSettings, base_url: str):
    scraper = Scraper(settings, FileStorageStrategy(), ConsoleNotificationStrategy())
    scraper.scrape_catalogue(base_url)
    scraper.storage_strategy.save("scraped_data.json","./data.json")
    scraper.notification_strategy.notify(f"Scraping complete. {len(scraper.scraped_data)} products scraped.")

    return {"message": "Scraping completed successfully.", "products_scraped": len(scraper.scraped_data)}
