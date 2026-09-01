"""
Personal Banking Ledger (CLI)
A simple beginner project to practice functions, if/else, loops, and lists.
"""


def deposit(balance, history, amount):
    """Add amount to balance if it's valid. Return the new balance."""
    if amount <= 0:
        print("Error: deposit amount must be greater than 0.")
        return balance

    balance = balance + amount
    history.append(f"Deposited {amount}")
    print(f"Deposited {amount}. New balance: {balance}")
    return balance


def withdraw(balance, history, amount):
    """Subtract amount from balance if valid and affordable. Return the new balance."""
    if amount <= 0:
        print("Error: withdrawal amount must be greater than 0.")
        return balance
    elif amount > balance:
        print("Error: insufficient funds for this withdrawal.")
        return balance
    else:
        balance = balance - amount
        history.append(f"Withdrew {amount}")
        print(f"Withdrew {amount}. New balance: {balance}")
        return balance


def check_balance(balance):
    """Print the current balance."""
    print(f"Current balance: {balance}")


def show_history(history):
    """Print every past transaction, one per line."""
    if len(history) == 0:
        print("No transactions yet.")
        return

    print("---- Transaction History ----")
    for entry in history:
        print(entry)


def main():
    """Run the menu loop."""
    balance = 0
    history = []

    while True:
        print("\n==== Personal Banking Ledger ====")
        print("1. Deposit")
        print("2. Withdraw")
        print("3. Check Balance")
        print("4. View History")
        print("5. Exit")

        choice = input("Choose an option: ")

        if choice == "1":
            amount_text = input("Amount to deposit: ")
            try:
                amount = float(amount_text)
            except ValueError:
                print("Error: please enter a valid number.")
                continue
            balance = deposit(balance, history, amount)

        elif choice == "2":
            amount_text = input("Amount to withdraw: ")
            try:
                amount = float(amount_text)
            except ValueError:
                print("Error: please enter a valid number.")
                continue
            balance = withdraw(balance, history, amount)

        elif choice == "3":
            check_balance(balance)

        elif choice == "4":
            show_history(history)

        elif choice == "5":
            print("Goodbye!")
            break

        else:
            print("Error: please choose a number from 1 to 5.")


if __name__ == "__main__":
    main()