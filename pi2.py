
import json


def load_accounts(filename):
  
    try:
        with open(filename, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def save_accounts(accounts, filename):

    with open(filename, "w") as file:
        json.dump(accounts, file, indent=2)


def create_account(accounts, name):
    if name in accounts:
        print(f"Error: account '{name}' already exists.")
        return

    accounts[name] = {"balance": 0, "history": []}
    print(f"Account '{name}' created.")


def deposit(accounts, name, amount):
   
    if name not in accounts:
        print(f"Error: account '{name}' does not exist.")
        return
    if amount <= 0:
        print("Error: deposit amount must be greater than 0.")
        return

    accounts[name]["balance"] += amount
    accounts[name]["history"].append(f"Deposited {amount}")
    print(f"Deposited {amount} into '{name}'. New balance: {accounts[name]['balance']}")


def withdraw(accounts, name, amount):
"
    if name not in accounts:
        print(f"Error: account '{name}' does not exist.")
        return
    if amount <= 0:
        print("Error: withdrawal amount must be greater than 0.")
        return
    if amount > accounts[name]["balance"]:
        print(f"Error: insufficient funds in '{name}'.")
        return

    accounts[name]["balance"] -= amount
    accounts[name]["history"].append(f"Withdrew {amount}")
    print(f"Withdrew {amount} from '{name}'. New balance: {accounts[name]['balance']}")


def transfer(accounts, from_name, to_name, amount):
  
    if from_name not in accounts or to_name not in accounts:
        print("Error: one or both accounts do not exist.")
        return
    if amount <= 0:
        print("Error: transfer amount must be greater than 0.")
        return
    if amount > accounts[from_name]["balance"]:
        print(f"Error: insufficient funds in '{from_name}' for this transfer.")
        return

    # Both checks passed, so it's safe to do both steps.
    withdraw(accounts, from_name, amount)
    deposit(accounts, to_name, amount)
    print(f"Transferred {amount} from '{from_name}' to '{to_name}'.")


def list_accounts(accounts):
    if len(accounts) == 0:
        print("No accounts yet.")
        return

    print("---- Accounts ----")
    for name in accounts:
        print(f"{name}: {accounts[name]['balance']}")


def show_history(accounts, name):
    if name not in accounts:
        print(f"Error: account '{name}' does not exist.")
        return

    history = accounts[name]["history"]
    if len(history) == 0:
        print(f"No transactions yet for '{name}'.")
        return

    print(f"---- History for '{name}' ----")
    for entry in history:
        print(entry)


def main():
    """Run the menu loop."""
    filename = "accounts.json"
    accounts = load_accounts(filename)

    while True:
        print("\n==== Multi-Account Budget Manager ====")
        print("1. Create Account")
        print("2. Deposit")
        print("3. Withdraw")
        print("4. Transfer")
        print("5. List Accounts")
        print("6. View History")
        print("7. Save & Exit")

        choice = input("Choose an option: ")

        if choice == "1":
            name = input("New account name: ")
            create_account(accounts, name)

        elif choice == "2":
            name = input("Account name: ")
            amount_text = input("Amount to deposit: ")
            try:
                amount = float(amount_text)
            except ValueError:
                print("Error: please enter a valid number.")
                continue
            deposit(accounts, name, amount)

        elif choice == "3":
            name = input("Account name: ")
            amount_text = input("Amount to withdraw: ")
            try:
                amount = float(amount_text)
            except ValueError:
                print("Error: please enter a valid number.")
                continue
            withdraw(accounts, name, amount)

        elif choice == "4":
            from_name = input("From account: ")
            to_name = input("To account: ")
            amount_text = input("Amount to transfer: ")
            try:
                amount = float(amount_text)
            except ValueError:
                print("Error: please enter a valid number.")
                continue
            transfer(accounts, from_name, to_name, amount)

        elif choice == "5":
            list_accounts(accounts)

        elif choice == "6":
            name = input("Account name: ")
            show_history(accounts, name)

        elif choice == "7":
            save_accounts(accounts, filename)
            print("Saved. Goodbye!")
            break

        else:
            print("Error: please choose a number from 1 to 7.")


if __name__ == "__main__":
    main()