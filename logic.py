import pandas as pd
import os
import gc


class Search:
    def __init__(self):
        self.uni_csv_paths = {}
        self.uni_sheets = {}
        self.my_id = None
        self.op_mode = "slow"


    def add_my_id(self, my_id: int):
        self.my_id = my_id


    def add_info(self, uni_path: str, uni_name: str, uni_degree: str, uni_is_budget: bool):
        uni_status = "бюджет" if uni_is_budget else "платка"

        if not os.path.isfile(uni_path):
            print(f"Файл по расположению {uni_path} не найден/отсутствует!")
            return

        self.uni_csv_paths[f"{uni_name} {uni_status}"] = {"path": uni_path, "name": uni_name, "degree": uni_degree, "is_budget": uni_is_budget}


    def set_fast_mode(self):
        self.op_mode = "fast"
        try:
            for uni in self.uni_csv_paths.keys():
                df = pd.read_csv(self.uni_csv_paths[uni]["path"], sep=";")
                self.uni_sheets[uni] = df
        except MemoryError:
            self.uni_sheets.clear()
            gc.collect()
            self.op_mode = "slow"
            print("Недостаточно оперативной памяти! Режим переключен на медленный (slow)!")
            return


    def set_slow_mode(self):
        self.op_mode = "slow"
        self.uni_sheets.clear()
        gc.collect()


    def find_abiturient_info_slow(self, abiturient_id: int):
        abiturient_data = {}

        for uni in self.uni_csv_paths.keys():
            df = pd.read_csv(self.uni_csv_paths[uni]["path"], sep=";")
            abiturient_line = df[df["Код поступающего"] == abiturient_id].iloc[0].to_dict()
            abiturient_data[uni] = abiturient_line

        return abiturient_data


    def find_abiturient_info_fast(self, abiturient_id: int):
        abiturient_data = {}
        
        for uni in self.uni_csv_paths.keys():
            df = self.uni_sheets[uni]
            abiturient_line = df[df["Код поступающего"] == abiturient_id].iloc[0].to_dict()
            abiturient_data[uni] = abiturient_line

        return abiturient_data
            

    def find_ab_info(self, abiturient_id: int):
        if self.op_mode == "slow":
            return self.find_abiturient_info_slow(abiturient_id)

        elif self.op_mode == "fast":
            return self.find_abiturient_info_fast(abiturient_id)