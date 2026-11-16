import numpy as np
import pandas as pd
import datetime
from datetime import datetime
import docx
from docx.shared import Inches, Cm
import pickle
import copy
import locale
from openpyxl import load_workbook
import xlsxwriter
from OMA_tools.io_data.dates import Dates_Operations
locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')


class File:
    def __init__(self, filename):
        self.filename = filename
        
    
    def from_file(self, skiprows, index_col = None):
        """
        This function reads .xlsx file with several lists
        Args:
            filename
            skiprows: the number of rows you want to skip
        Returns:
            A dict of pandas object
        """
        excel_reader = pd.ExcelFile(self.filename)
        data = {}
        for sheet_num, sheet_name in enumerate(excel_reader.sheet_names):
            data[sheet_num] = excel_reader.parse(sheet_name, index_col = index_col, skiprows = skiprows)
        #excel_reader.close()
        return data


        data = {}
        with pd.ExcelFile(self.filename) as excel_reader:
            for sheet_num, sheet_name in enumerate(excel_reader.sheet_names):
                data[sheet_num] = excel_reader.parse(sheet_name, index_col=index_col, skiprows=skiprows)
        return data
    
    
    def to_file(self, df):
        """
        This function writes pandas tables in several lists in excel.
        """
        with pd.ExcelWriter(self.filename, engine = 'openpyxl', mode = 'a', if_sheet_exists = 'replace') as excel_writer:
            for i, df_i in enumerate(df):
                df[df_i].to_excel(excel_writer, sheet_name = df_i)

                
    def to_file_by_list_names(self, df, list_names = []):
        """
        SECOND VARIANT OF WRITTING DATA TO FILE
        This function writes pandas tables in several lists in excel by using list_names
        Attention! The number of lists in list_names should be the same as in df.
        """
        with pd.ExcelWriter(self.filename, engine = 'xlsxwriter', mode = 'w') as excel_writer:
            for i, df_i in enumerate(df):
                df[i].to_excel(excel_writer, sheet_name = list_names[i])
                
                
    @staticmethod
    def to_file_from_dict(path, dict_data, file_names = []):
        """
        Writes pandas tables in several lists in excel by using list of BCA.
        Attention! The number of BCA should be the same as in dict_forecast.
        Args:
            path: path, where the file will be save
            dict_data: dict of DataFrames
            file_names: list of bca 
        """
        for file_name, data_df in zip(file_names, dict_data.values()):
            name = path + file_name + '.xlsx'
            with pd.ExcelWriter(name, engine = 'openpyxl', mode = 'a', if_sheet_exists = 'replace') as writer:
                    data_df.to_excel(writer)
    
       
    
    def update_file(self, data_new, column_name: str, list_of_replacements):
        """
        Function that updates your file with data
        Args:
            filename: file with data
            dataframe: DataFrame with new data that you want to add to file with data
            column_name: the name of column. Usually it is column with date.
            replacements_list: list with replacement keys for final dict
        Returns:
            data_new: updated data

        """
        data_old = self.from_file(skiprows = 0, index_col = 0)
        data_old = Dict_Operations(data_old).replace_keys_in_dict(list_of_replacements)     

        updated = {}

        # Итерируемся по ключам из нового словаря
        for key in data_new.keys():

            new = pd.DataFrame()

            
            if key not in data_old:
                print(f"Ключ {key} не найден в старом файле")
                continue

            df_new = data_new[key].copy()
            df_excel = data_old[key].copy()  # Используем обновляемый словарь

            df_new[column_name] = pd.to_datetime(df_new[column_name], errors = 'coerce')
            df_excel[column_name] = pd.to_datetime(df_excel[column_name], errors = 'coerce')


            new_unique_dates = df_new[column_name].unique()
            old_unique_dates = df_excel[column_name].unique()

            old_ones = []

            # Фильтруем даты, которые уже присутствуют в данных
            for new_date in new_unique_dates:
                    
                if new_date in old_unique_dates:
                    old_ones.append(pd.to_datetime(new_date))

            if len(old_ones) != 0:
                min_date_str = min(old_ones).strftime('%Y-%m-%d')

                # Оставляем только те даты, которые не встречаются в новых, если таковые нашлись
                filtered = df_excel[df_excel[column_name] < min_date_str]

                if len(filtered) != 0:
            
                    # Обновляем таблицу с фактическими данными
                    new = pd.concat([filtered, df_new]).reset_index(drop = True)
            
            # В противном случае просто добавляем новые данные в конец старой таблицы
            else:
                new = pd.concat([df_excel, df_new]).reset_index(drop = True)

            updated[key] = new
        
        # Сохраняем в файл
        try:
            self.to_file(updated)
        except Exception as e:
            print(f"Не удалось сохранить Excel: {e}")
        
        return updated
    
    
    @staticmethod
    def generate_filename(filename_start: str, file_format: str):
        """
            Generates filename with today date
            Args:
                filename_start: filename without date
                file_format: format of output file
        """
        now = datetime.today()
        formatted = now.strftime("%d.%m.%Y")
        output_filename = filename_start + formatted + file_format
        return output_filename
    
    
    @staticmethod
    def set_style_doc_file(doc, top_margin, bottom_margin, left_margin, right_margin):
        """
        Sets width of paper in file .docx
        """
        sections = doc.sections
        for section in sections:
            section.top_margin = Cm(top_margin)
            section.bottom_margin = Cm(bottom_margin)
            section.left_margin = Cm(left_margin)
            section.right_margin = Cm(right_margin)

    
class Table:
    def __init__(self, df):
        self.df = df
        
    
    def sort_in_specific_way(self, desired_sequence: list, column: str, date_column = None):
        """
            Функция для сортировки в нужном порядке в столбце DataFrame, когда обычный метод sort_values не работает
        """
        # Преобразуем столбец "Месяц" в категориальный тип с заданным порядком
        self.df[column] = pd.Categorical(self.df[column], categories = desired_sequence, ordered = True)

        if date_column is not None:
            # Сортируем датафрейм по столбцу "Месяц"
            df_sorted = self.df.sort_values([column, date_column])

            # Сбрасываем индексы, если нужно
            df_sorted.reset_index(drop = True, inplace = True)
            return df_sorted
        else:
            # Сортируем датафрейм по столбцу "Месяц"
            df_sorted = self.df.sort_values(by = column)

            # Сбрасываем индексы, если нужно
            df_sorted.reset_index(drop = True, inplace = True)
            return df_sorted

    

    def split_column(self, split, col_name, col_name_new):
        """
        Function that splits column into two other columns
        """
        new_df = self.df[col_name].str.split(split, expand = True)
        new_df.columns = col_name_new
        return new_df
    
    
    def delete_rows_with_substring(self, col_name, substring):
        """
        Function deletes rows with substring
        """
        for index, row in self.df.iterrows():
            if substring in row[col_name]:
                self.df = self.df.drop([index], axis = 0)
        return self.df 
    
    
    def convert_date_column_from_str_to_datetime(self, name_column_date: str):
        """
        Converts dates from format 'Январь 2021' to '01.01.2021'
        """
        dates = list(self.df[name_column_date])
        dates_converted = []
        for i in dates:
            dates_converted.append(datetime.strptime(i, '%B %Y').strftime('%d.%m.%Y'))

        #Замена столбца дат на новый конвертированный столбец
        self.df[name_column_date] = self.df[name_column_date].replace(dates, dates_converted)
        return self.df
    
    
    @staticmethod
    def make_left_join(df1, df2, df3, key):
        """
        This function will make left join for 3 DataFrames
        """
        joined_df = pd.merge(df1, df2, on = key, how = 'left')
        left_joined_df = pd.merge(joined_df, df3, on = key, how = 'left')  
        return left_joined_df
    
    
    def make_table(self, column_name):
        """
        Makes a table with columns as in files with fact data
        Works especially for pivot tables 
        Args:
            column_name: rename column with index into new one, str
        """
        data = self.df.rename_axis(None, axis = 0)
        data.columns = data.columns.droplevel(0)
        data.reset_index(inplace = True)
        data = data.rename(columns = {'index': column_name})
        return data
    
    
    @staticmethod
    def update_table(data_old, data_new, column_name):
        """
            Updates table
        """
        for col_name in data_old.columns:
            if 'Unnamed' in col_name:
                data_old = data_old.drop(columns=[col_name])
        for col_name in data_new.columns:
            if 'Unnamed' in data_new:
                data_new = data_new.drop(columns=[col_name])
                
        for i, row_i in data_new.iterrows():
            data_old[data_old[column_name] == row_i[0]] = data_new[data_new[column_name] == row_i[0]]

        if data_old.iloc[-1][column_name] == data_new.iloc[0][column_name]: 
                data_old.iloc[-1] = data_new.iloc[0]
                data_old = pd.concat([data_old, data_new.iloc[1:]])
        else:
            data_old = pd.concat([data_old, data_new])
        data_old[column_name] = data_old[column_name].apply(lambda x: pd.to_datetime(x))
        data_old = data_old.dropna()
        data_old = data_old.reset_index(drop = True)
        data_old = data_old.drop_duplicates()
        return data_old
    

    def make_style_of_table(
            self, 
            writer, 
            sheet_name: str, 
            width_col_1: float, 
            width_col_2: float, 
            width_col_3: float, 
            num_format = '0.0000', 
            column_start = 2):
        """
        Args:
            filename: file with dataframe
            sheet_name: sheet name of table
            width_col_1: column width of column A in Excel
            width_col_2: column width of column B in Excel
            width_col_3: column width of all columns except column A and B
        """
        #data = File(filename).from_file(0, 0)
        #df = data[0]
        #writer = pd.ExcelWriter(filename, engine = 'xlsxwriter')
        self.df.to_excel(writer, sheet_name = sheet_name, startrow = 1, header = False)

        workbook = writer.book
        worksheet = writer.sheets[sheet_name]

        header_format = workbook.add_format({
                                            'bold': True,
                                            'text_wrap': True,   # перенос текста
                                            'align': 'center',   # выравнение текста в ячейке
                                            'valign': 'vcenter', # выравнение текста в ячейке
                                            'border': 0
                                            #'center_across': True
                                        })
        table_fmt = workbook.add_format({'num_format': num_format, 
                                         'align': 'center',        # выравнение текста в ячейке
                                         'valign': 'vcenter',      # выравнение текста в ячейке
                                         'border': 0
                                         #'center_across': True
                                         })

        for col_num, value in enumerate(self.df.columns.values):
            worksheet.write(0, col_num + 1, value, header_format)

        for col in range(len(self.df.columns)):
            writer.sheets[sheet_name].set_column(1,  col + 1, width_col_3, table_fmt)
            #writer.sheets[sheet_name].set_column(column_start, col + 1, width_col_3, table_fmt)
        worksheet.set_column('A:A', width_col_1)
        worksheet.set_column('B:B', width_col_2)
        #writer.close()
    
    
    
class Dict_Operations:
    def __init__(self, dictionary):
        self.dictionary = dictionary
    
    
    def replace_keys_in_dict(self, list_of_replacements = []):
        """
        Static method of class to replace keys on the new ones in dict
        """
        dict_new = dict(zip(list_of_replacements, list(self.dictionary.values())))
        return dict_new
    
    
    def sort_keys_in_dict(self, sorted_keys = []):
        """
            Sorts keys in dict in your own way.
            Args:
                sorted_keys: list of key in the new way
        """
        dict_new = {}
        for k in sorted_keys:
            if k in self.dictionary.keys():
                dict_new[k] = self.dictionary[k]
        return dict_new

    
    @staticmethod
    def generate_list_or_dict_from_df(dict_with_df, col_name, cond):
        """
        Generate list or dict from DataFrame
        Args:
            col_name: column name which you want to convert to list or dict
            cond: condition list or dict
        """
        dict_data = {}
        if cond == 'list':
            for key, value in dict_with_df.items():
                dict_data[key] = list(map(lambda x: str(x), np.array(dict_with_df[key][col_name]).tolist()))
            return dict_data
        elif cond == 'dict':
            for key, value in dict_with_df.items():
                dict_data[key] = dict_with_df[key].set_index(col_name).T.to_dict('list')

                for k, v in dict_data[key].items():
                    dict_data[key][k] = dict_data[key][k][0]
            return dict_data
    
    
    def copy_columns_df_from_dict(self):
        """
        If you have got a lot of columns in your dataframe, you need to copy them for future operations
        """
        dict_columns = {}
        for key, df in self.dictionary.items():
            dict_columns[key] = copy.copy(list(df.columns))
        return dict_columns
    
    
    def combine_dict_into_one_dataframe(self, column_name: str):
        
        """
        Make join of dict with several dataframes by column (usually with date) and delete duplicates 
           Args:
           column_name: name of column by which you want to merge all dataframes in dict
           Returns:
           Merged DataFrame
        """
        for bca, data in self.dictionary.items():
            data.columns = [str(col) + ' ' + bca for col in data.columns]
            data.columns = data.columns.str.replace(column_name + ' ' + bca, column_name, regex = False)

        data_list = [data for key, data in self.dictionary.items()]
        #Объединение всех датафреймов в один и удаление дубликатов столбцов с датами
        data = pd.concat(data_list, axis = 1).drop_duplicates().reset_index(drop = True)
        merged_df = data.T.drop_duplicates(keep = 'first').T

        #Изменение типа данных в столбцах
        for column in merged_df.columns[1:]:
            merged_df[column] = merged_df[column].astype(float)
        return merged_df
    
    
    @staticmethod
    def save_dict_to_pkl(dict_data, filename):
        """
        Saves dict to .pkl file
        """
        dict_columns = {}
        for key, df in dict_data.items():
            dict_columns[key] = copy.copy(list(df.columns))

        with open(filename, 'wb') as f:
            pickle.dump(dict_columns, f)
     
    
    @staticmethod
    def load_pkl_file(filename):
        """
        Load .pkl file
        Args:
            file .pkl
        Returns:
            dictionary
        """
        with open(filename, 'rb') as f:
            loaded_dict_columns = pickle.load(f)
        return loaded_dict_columns
    
    
    def rename_columns_in_dict_with_df(self, dict_columns):
        """
        Place columns in a specific way from another dictionary
        """
        df_dict = {}
        for key, data in self.dictionary.items():
            df_dict[key] = data[dict_columns[key]]
        return df_dict
    
    
    def convert_column_with_date(self, col_name_with_date):
        """
            Converts Date 2021-01-01 to January 2021
        """
        months_ru = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        for key, df in self.dictionary.items():
            new_dates = []
            
            for date_val in df[col_name_with_date]:
                if pd.notna(date_val):
                    month_num = date_val.month
                    year = date_val.year
                    new_dates.append(f"{months_ru[month_num]} {year}")
                else:
                    new_dates.append('')
            
            self.dictionary[key][col_name_with_date] = new_dates
        
        return self.dictionary
    
    
    def rename_items_in_dict(self, dict_names_init):
        """
            Функция для замены значений в одном словаре с использованием другого словаря, где во втором словаре - значения - нужные нам.
        """
        dict_new = {}
        #Замена названий каналов в словарях
        for key, items_old in self.dictionary.items():
            items_new = [dict_names_init.get(item, item) for item in items_old]
            dict_new[key] = items_new
        return dict_new

    
    @staticmethod
    def make_concat_of_dicts_dataframes(dict_1, dict_2):
        """
        Makes pd.concat of two dicts
        Args:
            dict_1, dict_2: value is dataframe; key are identical!
        """
        res = {}
        for (key, df_1), (key, df_2) in zip(dict_1.items(), dict_2.items()):
            res[key] = pd.concat([df_1, df_2]).reset_index(drop = True)
        return res

