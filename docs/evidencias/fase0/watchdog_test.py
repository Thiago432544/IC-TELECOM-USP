from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import socket
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

print("watchdog_test")
def restart_router():
	
	with open("/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log", "a") as f:
			print("reinicializando roteador", file=f)
	
	try:
		options = Options()
		options.binary_location = "/usr/bin/chromium"

		service = Service("/usr/bin/chromedriver")

		driver = webdriver.Chrome(
			service=service,
			options=options
		)
		
		driver.get("http://192.168.11.254/index.html?status")
		
		time.sleep(10)
		button = driver.find_element(By.XPATH,"/html/body/div[1]/div[2]/form/div[1]/div[3]/input");
		button.click()
		time.sleep(10)
		
		button = driver.find_element(By.XPATH,"/html/body/div[1]/div[2]/form/div[5]/div[2]/table/tbody/tr[7]/td[2]/input");
		
		button.click()
		
		with open("/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log", "a") as f:
			print("roteador reiniciado com sucesso", file=f)
			
		time.sleep(10)
		driver.quit()

		return 0;
	except Exception as e:
		
		with open("/home/pi/Desktop/Porto_de_Santos_2025/PRINCIPAL/router.log", "a") as f:
			print(f"Erro ao reiniciar o roteador: {e}", file=f)
		driver.quit()
		return -1
	
	
if __name__ == "__main__":
    restart_router()