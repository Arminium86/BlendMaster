# This is the control centre in which user and inventory data are imported and the program is executed

class Run:
    def execute(self, start_time, expit_mode, file_path, blend_mode, stockpile_data, calendar_inputs):
        # Install required libraries
        from classes.CaseModeller import CaseModeller
        from classes.DataLoader import DataLoader
        from classes.PeriodManager import PeriodManager
        from classes.ExpitDataHandler import ExpitDataHandler
        from database.SQLiteDatabase import DatabaseManager
        from execute.Requirements import Requirements

        requirements = Requirements()
        requirements.install_requirements()

        # Initialize periods
        periods = PeriodManager()
        periods.calculate_periods(start_time)

        # Process APS expit data (mining.csv)
        expit_data_handler = ExpitDataHandler(file_path)
        expit_payload_transactions = expit_data_handler.process_transactions()

        # User interaction required to choose between original time and updated time methods
        user_interaction_mode = expit_mode

        # Cast user choice to appropriate type
        try:
            user_interaction_mode = int(user_interaction_mode)
        except ValueError:
            print("Invalid input. Please enter a number.")

        database_manager = DatabaseManager()

        if user_interaction_mode == 2:

            expit_payload_transactions = expit_data_handler.update_transactions(expit_payload_transactions, start_time)
            expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        elif user_interaction_mode == 1:
            expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        else: 
            print("Invalid input. Please enter a number.")
            return  # Exit execution for invalid input

        # Load input data (this is combined user input and opening inventories)
        input_data = DataLoader(stockpile_data, calendar_inputs, expit_payload_transactions)

        stockpile_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        # Initialize and run CaseModeller
        case_modeller = CaseModeller(
            stockpiles=stockpile_data_objects,
            grade_blocks=[], # Placeholder
            equipment=equipment_data_objects,
            crusher_targets=crusher_target_data,
            expit_payload_transactions=expit_payload_transactions,
            periods=periods,
            user_interaction_mode=blend_mode
        )

        case_modeller.run()
