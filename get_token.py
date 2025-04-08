from vkpymusic import TokenReceiver

login = input("   Enter login: ")
password = input("Enter password: ")

tokenReceiver = TokenReceiver(login, password)

if tokenReceiver.auth():
    token = tokenReceiver.get_token()
    tokenReceiver.save_to_config()
    print("\nтокен: " + token)
    print("Токен сохранен в конфигурации и может быть добавлен в .env файл")