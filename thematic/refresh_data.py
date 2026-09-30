import pandas as pd
import numpy as np
import xlsxwriter
import datetime
from datetime import datetime
import calendar

from OMA_tools.io_data.operations import *
from OMA_tools.io_data.colors import *

from OMA_tools.thematic.share import *
from OMA_tools.regions.data_extraction.task_builder import *

import warnings
warnings.filterwarnings('ignore')



class DataUpdater:
    """
        Класс для обновления файлов с фактическими данными по Тематическому ТВ
    """
    def __init__(self, guide_book: pd.DataFrame, current_year: int):
        """
            Атрибуты:
            ----------
                guide_book: pd.DataFrame
                    Таблица-справочник с перечнем все каналом и их описанием
                current_year: int
                    Номер текущего года, по которому хотим обновить данные в истории
        """

        self.guide_book = guide_book
        self.current_year = current_year
        
        self.MONTHS = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }

        self.VK_DICT = {
            'ЕРК': 'все 25-49',
            'ЖРК': 'ж 25-49',
            'МРК': 'м 25-49',
            'ДРК': 'все 4-40',
        }


    def parse_SQL_file(self, sql_file_path: str):
        """
            Метод для парсинга ежедневной выгрузки со странным названием, которая приходит с адреса NVDBMZ\MZ SQL server <vimb2-sql-svc-mz@vitpc.com>.
            !!! ВАЖНО !!! Очень важно брать последнюю выгрузку для закрытого месяца, чтобы была возможность закрывать прошлый месяц.

            Параметры:
            ----------
                sql_file_path: str
                    Путь к файлу с выгрузкой SQL в формате .csv
            
            Returns:
            ----------
                result_sorted: pd.DataFrame
                    Причёсанная SQL выгрузка в виде pd.DataFrame
        """
        sql_data = pd.read_csv(
            sql_file_path,
            encoding = 'utf-16', names = ['raw'],
            sep = '\t',  # разделитель столбцов - табуляция
            decimal = ','  # десятичный разделитель - запятая
        )

        # Удаляем заголовок
        sql_data = sql_data.iloc[1:].reset_index(drop = True)

        # Разделяем по первому TAB (самый левый разделитель)
        split_data_left = sql_data['raw'].str.split(' ', n = 1, expand = True)
        split_data_left.columns = ['ID канала', 'rest']  # переименовываем

        # Разделяем по первому TAB (самый левый разделитель)
        TVR = split_data_left['rest'].str.rsplit(' ', n = 1, expand = True)
        TVR.columns = ['rest', 'Ср.рейтинг эфира']

        tvr = TVR['rest'].str.rsplit(' ', n = 1, expand = True)
        tvr.columns = ['rest', 'Ср.рейтинг рекламы']

        hour = tvr['rest'].str.rsplit(' ', n = 1, expand = True)
        hour.columns = ['rest', 'Час']

        month = hour['rest'].str.rsplit(' ', n = 1, expand = True)
        month.columns = ['rest', 'Месяц']

        year = month['rest'].str.rsplit(' ', n = 1, expand = True)
        year.columns = ['rest', 'Год']

        id_bca = year['rest'].str.rsplit(' ', n = 1, expand = True)
        id_bca.columns = ['Канал', 'ID ЦА']


        # Шаг 3: ОБЪЕДИНЯЕМ все столбцы
        final_result = pd.DataFrame({
            'ID канала': split_data_left['ID канала'],
            'Канал': id_bca['Канал'],
            'ID ЦА': id_bca['ID ЦА'],
            'Год': year['Год'],
            'Месяц': month['Месяц'],
            'Час': hour['Час'],
            'tvr рекламный': tvr['Ср.рейтинг рекламы'],
            'TVR эфира': TVR['Ср.рейтинг эфира']
        })

        result_filtered = final_result[ final_result['Час'] == 'NULL' ].reset_index(drop = True)

        int_columns = ['ID канала', 'ID ЦА', 'Год', 'Месяц']
        for int_column in int_columns:
            result_filtered[int_column] = result_filtered[int_column].astype(int)
            
        float_columns = ['tvr рекламный', 'TVR эфира']
        for float_column in float_columns:
            result_filtered[float_column] = result_filtered[float_column].str.replace(',', '.')
            result_filtered[float_column] = result_filtered[float_column].astype(float)

        # Создаем столбец с пустыми значениями
        result_filtered['ЦА'] = ''

        result_filtered.loc[result_filtered['ID ЦА'] == 158, 'ЦА'] = 'все 25-49'
        result_filtered.loc[result_filtered['ID ЦА'] == 856, 'ЦА'] = 'ж 25-49'
        result_filtered.loc[result_filtered['ID ЦА'] == 857, 'ЦА'] = 'м 25-49'
        result_filtered.loc[result_filtered['ID ЦА'] == 571, 'ЦА'] = 'все 4-40'

        result_filtered = result_filtered.drop(['Час'], axis = 1)
        result_sorted = result_filtered.sort_values(by = 'Канал')
        return result_sorted.reset_index(drop = True)
    

    def tvr_last_fact_month(self, sql_df: pd.DataFrame):
        """
            Метод для формирования фактических значений tvr по последнему месяцу
        
            Параметры:
            ----------
                sql_df: pd.DataFrame
                    Таблица с выгрузкой SQL
            
            Returns:
            ----------
                new_month_data: dict
                    Словарь с данными по ново закрывшемуся месяцу: 
                        key - название ВК
                        value - pd.DataFrame
        """
        new_month_data = {}
        for VK, bca in self.VK_DICT.items():
            # ШАГ 1. Отбираем каналы ВК для анализа из Справочника
            target_guide = self.guide_book[self.guide_book[VK] == 1].reset_index(drop = True)
            target_guide = target_guide[['Канал', VK]]
            
            # ШАГ 2.  Отбираем ЦА для анализа из SQL выгрузки
            target_sql = sql_df[sql_df['ЦА'] == bca].reset_index(drop = True)

            # ШАГ 3. Join с каналами из справочника
            merged_sql = pd.merge(target_sql, target_guide, on = 'Канал', how = 'inner')

            # Задаем название месяца, который будем закрывать
            self.month_close = f"{self.MONTHS[merged_sql.iloc[0]['Месяц']]} {self.current_year}"
            merged_sql.rename(columns = {'tvr рекламный': self.month_close}, inplace = True)

            merged_sql_filtered = merged_sql[['Канал', self.month_close]]
            merged_sql_sorted = merged_sql_filtered.sort_values(by = 'Канал')
            
            new_month_data[VK] = merged_sql_sorted.reset_index(drop = True)
        
        return new_month_data
    

    def update_historical_data(self, new_data_dict: dict,  history_full: str, history_curr_year: str):
        """
            Метод для обновления файлов с фактическими данными по всей истории, а также истории по текущему году.

            Параметры:
            ----------
                new_data_dict: dict
                    Словарь с новыми данными, где ключ - название ВК, значение - датафрейм с данными.
                history_full: str
                    Путь к файлу с историей, начиная с 2021г.
                history_curr_year: str
                    Путь к файлу с историей по текущему году.
            
            Returns:
            ----------

        """
        # Проверяем, существует ли файл с историей, начиная с 2021 г
        if not os.path.exists(history_full):
            print(f"📁 Файл {history_full} не найден. Будет создан новый файл с текущими данными")
            return new_data_dict
        
        # Проверяем, существует ли файл с историей по текущему году
        if not os.path.exists(history_curr_year):
            print(f"📁 Файл {history_curr_year} не найден. Будет создан новый файл с текущими данными")
            return new_data_dict
            
        data_dict = File(history_curr_year).from_file(0)
        hist_curr_year_dict = Dict_Operations(data_dict).replace_keys_in_dict(['ЕРК', 'ЖРК', 'МРК', 'ДРК'])

        full_data_dict = File(history_full).from_file(0)
        full_hist_dict = Dict_Operations(full_data_dict).replace_keys_in_dict(['ЕРК', 'ЖРК', 'МРК', 'ДРК'])

        updated_curr_year_hist = {}
        updated_full_hist = {}

        for vk, curr_year_hist_df in hist_curr_year_dict.items():
            full_hist_df = full_hist_dict[vk]
            # Отбираем свежие данные по конкретному ВК
            table = new_data_dict[vk]

            new_channels = set(table['Канал']) - set(curr_year_hist_df['Канал'])  # Новые каналы (есть в справочнике, нет в истории)

            # Если добавились новые каналы
            if len(new_channels) > 0:
                print(Color.BOLD + Color.GREEN + "Найдены следующие новые каналы. Заполню нулями историю для этих каналов." + Color.END)
                for ch in new_channels:
                    print(f"  - {ch}")
                    # Создаем новую строку для каждого нового канала.
                    # Для всех месяцев в факте заполняем ее нулями
                    full_hist_df[ch] = [0.0] * (full_hist_df.shape[1] - 1)
                    curr_year_hist_df[ch] = [0.0] * (curr_year_hist_df.shape[1] - 1)

            # ОБНОВЛЕНИЕ ТЕКУЩЕГО ГОДА
            curr_year_updated = pd.merge(curr_year_hist_df, table, on = 'Канал', how = 'left')
            curr_year_updated_sorted = curr_year_updated.sort_values(by = 'Канал')
            curr_year_updated_sorted = curr_year_updated_sorted.fillna(0)
            updated_curr_year_hist[vk] = curr_year_updated_sorted


            # ОБНОВЛЕНИЕ ИСТОРИИ, НАЧИНАЯ С 2021г
            full_hist_updated = pd.merge(full_hist_df, table, on = 'Канал', how = 'left')
            # Переставляем колонки, чтобы новый столбец был слева
            full_hist_updated_sorted = full_hist_updated.sort_values(by = 'Канал')
            full_hist_updated_sorted = full_hist_updated_sorted.fillna(0)
            updated_full_hist[vk] = full_hist_updated_sorted

        return updated_curr_year_hist, updated_full_hist
    

    def pipeline_updater(self, sql_file_path: str, history_full_path: str, history_curr_year_path: str):
        """
            Пайплайн для обновления файлов с историческими данными
        """
        # ШАГ 1. Парсинг файла с выгрузкой SQL
        sql_df = self.parse_SQL_file(sql_file_path)

        # ШАГ 2. Получение данных по последнему закрывшемуся месяцу из выгрузки SQL
        new_tvr_dict = self.tvr_last_fact_month(sql_df)

        # ШАГ 3. Обновление таблиц с историческими данными
        curr_year_dict_updated, full_hist_dict_updated = self.update_historical_data(new_tvr_dict, history_full_path, history_curr_year_path)

        # ШАГ 4. Сохранение данных обратно в файл
        ThematicShare.save_with_xlsxwriter(curr_year_dict_updated, history_curr_year_path)
        ThematicShare.save_with_xlsxwriter(full_hist_dict_updated, history_full_path)

        return new_tvr_dict


class TTVUpdater:
    """
        Класс для обновления исторических файлов со статистикой TTV.
        !!! ВАЖНО!!! Выгружаются данные помесячно!
    """
    def __init__(self, date_filter: list):
        """
            Атрибуты класса

            Параметры:
            ----------
                date_filter: list
                    Период для выгрузки данных
        """
        self.date_filter = date_filter

        # Интервал вещания
        self.time_filter_adult = 'timeBand1 >= 60000 AND timeBand1 < 260000'      # 06:00 - 26:00
        self.time_filter_children = 'timeBand1 >= 60000 AND timeBand1 < 220000'   # 06:00 - 22:00

        # Задаем каналы: ВРЕМЯ, ТЕЛEКАФЕ, ПОЕХАЛИ!
        self.company_filter = f'tvCompanyId IN (1873)'

        # Указываем список статистик для расчета
        self.statistics = ['TTVRtgPer']

        # Указываем срезы
        self.slices = ['researchMonth'] #Разбиваем по телекомпаниям

        # Задаем условия сортировки: телекомпания (от а до я)
        self.sortings = {"researchMonth":"ASC"}

        # Задаем опции расчета
        self.options = {
            "kitId": 1, #TV Index Plus All Russia
            "totalType": "TotalChannels", #Расчет Share от Total Channels. 
        }


        # Задаем переменные, которые NAN
        self.weekday_filter = None #фильтр на дни недели
        self.daytype_filter = None #фильтр на тип дня
        self.basedemo_filter = None #ЦА
        self.basedemo_filter_children = 'age >= 4 and age <= 40' #ЦА
        self.targetdemo_filter = None #доп фильтр на ЦА для расчета Affinity Index
        self.location_filter = None #место просмотра, если None => Дом, Дача
        self.daytype_filter = None


        # Словарь с целевыми аудиториями: ключ - название переменной (target), значение - ее синтаксис (syntax)
        self.targets = {
            'ВСЕ 25-49':'age >= 25 AND age <= 49',
            'М 25-49':'age >= 25 AND age <= 49 AND sex = 1',
            'Ж 25-49':'age >= 25 AND age <= 49 AND sex = 2'
        }

        # Форматируем как "Месяц Год" на русском
        self.months_ru = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
    

    def generate_month_name(self):
        """
            Метод для генерации названия выгружаемого месяца в формате "Месяц Год" (Июнь 2026)
        """
        # Берём первую дату из первого кортежа
        date_str = self.date_filter[0][0]

        # Преобразуем в объект datetime
        dt = datetime.strptime(date_str, '%Y-%m-%d')

        new_column_name = f"{self.months_ru[dt.month]} {dt.year}"
        return new_column_name
    

    def make_api_calculation(self):
        """
            Метод для выгрузки данных из БД Mediascope
        """
        # ШАГ 1. Определение названия колонки с выгружаемым месяцем
        new_column_name = self.generate_month_name()

        # ШАГ 2. Выгрузка данных для взрослой аудитории
        adult_tasks = BaseDataService._build_timeband_common_params(
            date_filter = self.date_filter, company_filter = self.company_filter, 
            basedemo_filter = self.basedemo_filter, regions_id = None,          # работаем в Федеральной Базе
            targets = self.targets, time_filter = self.time_filter_adult, 
            statistics = self.statistics, slices = self.slices, 
            sortings = self.sortings, options = self.options,
            location_filter = self.location_filter, weekday_filter = self.weekday_filter,
            daytype_filter = self.daytype_filter, targetdemo_filter = self.targetdemo_filter,
            add_city_to_basedemo_from_region = False,
            add_city_to_targetdemo_from_region = False
        )
        # Отправляем задачи на расчет
        df_adult = BaseDataService._execute_tasks(adult_tasks)
        df_adult['Месяц'] = new_column_name
        df_adult = df_adult.drop(['researchMonth'], axis = 1)
        df_adult.rename(columns = {'prj_name': 'ЦА', 'TTVRtgPer': 'TTV'}, inplace = True)

        # ШАГ 3. Выгрузка данных для детской аудитории
        child_tasks = BaseDataService._build_timeband_common_params(
            date_filter = self.date_filter, company_filter = self.company_filter, 
            basedemo_filter = self.basedemo_filter_children, regions_id = None,          # работаем в Федеральной Базе
            targets = None, time_filter = self.time_filter_children, 
            statistics = self.statistics, slices = self.slices, 
            sortings = self.sortings, options = self.options,
            location_filter = self.location_filter, weekday_filter = self.weekday_filter,
            daytype_filter = self.daytype_filter, targetdemo_filter = self.targetdemo_filter,
            add_city_to_basedemo_from_region = False,
            add_city_to_targetdemo_from_region = False
        )
        # Отправляем задачи на расчет
        df_child = BaseDataService._execute_tasks(child_tasks)
        df_child['prj_name'] = np.where(df_child['prj_name'] == 'Total. Ind', 'ВСЕ 4-40', df_child['prj_name'])
        df_child['Месяц'] = new_column_name
        df_child = df_child.drop(['researchMonth'], axis = 1)
        df_child.rename(columns = {'prj_name': 'ЦА', 'TTVRtgPer': 'TTV'}, inplace = True)

        # ШАГ 4. Совмещение результатов в одну единую таблицу
        result_df = pd.concat([df_adult, df_child]).reset_index(drop = True)

        result_df_pivot = result_df.pivot_table(
            index = 'ЦА', 
            columns = 'Месяц', 
            values = 'TTV'
        ).reset_index()
        result_df_pivot['ЦА'] = result_df_pivot['ЦА'].str.lower()

        return result_df_pivot


    def update_table(self, new_data_df: pd.DataFrame, hist_file_path_full: str, hist_file_path_curr_year: str):
        """
            Метод для обновления таблицы с историческими данными.

            Параметры:
            ----------
                new_data_df: pd.DataFrame
                    Таблица с ново-выгруженными данными
                hist_file_path_full: str
                    Путь к файлу с историческими данными, начиная с 2021 г.
                hist_file_path_curr_year: str
                    Путь к файлу с историческими данными по текущему году.
            
            Returns:
                ttv_df_full_hist_updated: pd.DataFrame
                    Обновленная таблица со всеми историческими данными с 2021 г
                ttv_df_curr_year_updated: pd.DataFrame
                    Обновленная таблица с историческими данными по текущему году
        """
        # ШАГ 1. Проверка, что файлы существуют
        # Проверяем, существует ли файл с историей, начиная с 2021 г
        if not os.path.exists(hist_file_path_full):
            print(f"📁 Файл {hist_file_path_full} не найден. Будет создан новый файл с текущими данными")
            return new_data_df
        
        # Проверяем, существует ли файл с историей по текущему году
        if not os.path.exists(hist_file_path_curr_year):
            print(f"📁 Файл {hist_file_path_curr_year} не найден. Будет создан новый файл с текущими данными")
            return new_data_df
        
        # ШАГ 2. Чтение файлов с историческими данными
        ttv_df_full_hist = pd.read_excel(hist_file_path_full)
        ttv_df_curr_year = pd.read_excel(hist_file_path_curr_year)

        # ШАГ 3. Обновление данных
        ttv_df_full_hist_updated = pd.merge(ttv_df_full_hist, new_data_df, on = 'ЦА', how = 'left')
        ttv_df_full_hist_updated = ttv_df_full_hist_updated.fillna(0)

        ttv_df_curr_year_updated = pd.merge(ttv_df_curr_year, new_data_df, on = 'ЦА', how = 'left')
        ttv_df_curr_year_updated = ttv_df_curr_year_updated.fillna(0)

        return ttv_df_full_hist_updated, ttv_df_curr_year_updated
    
    
    def save_with_xlsxwriter(self, df: pd.DataFrame, sheet_name = 'TTV', filename = 'output.xlsx'):
        # 1. СОЗДАЕМ ФАЙЛ С УНИКАЛЬНЫМ ИМЕНЕМ (никогда не будет конфликтов)
        base, ext = os.path.splitext(filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_filename = f"{base}_temp_{timestamp}{ext}"
        
        #print(f"📁 Создаю временный файл: {temp_filename}")
        
        try:
            workbook = xlsxwriter.Workbook(temp_filename, {
            'nan_inf_to_errors': True,
            'constant_memory': True
            })
            
            # Очищаем данные
            df_clean = df.copy()
            df_clean = df_clean.where(pd.notnull(df_clean), None)
            df_clean = df_clean.replace([np.inf, -np.inf], None)
            
            # Создаем лист
            worksheet = workbook.add_worksheet(sheet_name[:31])
            
            # ============ ФОРМАТЫ ============
            # Формат для заголовков
            header_fmt = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#D9E1F2',
                'border': 1,
                'border_color': '#4472C4',
                'font_size': 11,
                'font_name': 'Arial'
            })
            
            # Формат для первого столбца (каналы)
            left_fmt = workbook.add_format({
                'align': 'left',
                'valign': 'vcenter',
                'font_size': 10,
                'font_name': 'Arial'
            })
            
            # Формат для чисел
            center_fmt = workbook.add_format({
                'align': 'center',
                'valign': 'vcenter',
                'num_format': '0.000000',
                'font_size': 10,
                'font_name': 'Arial'
            })
            
            # Формат для нулевых значений
            zero_fmt = workbook.add_format({
                'bg_color': '#FFBDBD',
                'font_color': '#8B0000',
                'bold': False,
                'align': 'center',
                'valign': 'vcenter',
                'num_format': '0.000000',
                'font_size': 10,
                'font_name': 'Arial'
            })
            
            # ============ ЗАПИСЬ ДАННЫХ ============
            
            # 1. Пишем заголовки (строка 0)
            for col_idx, col_name in enumerate(df_clean.columns):
                worksheet.write(0, col_idx, str(col_name), header_fmt)
            
            # 2. Пишем данные (начиная со строки 1)
            for row_idx, row in enumerate(df_clean.values, start=1):
                for col_idx, value in enumerate(row):
                    # Пропускаем None
                    if value is None or (isinstance(value, float) and np.isnan(value)):
                        continue
                    
                    # Выбираем формат
                    if col_idx == 0:
                        fmt = left_fmt
                    else:
                        fmt = center_fmt
                    
                    # Записываем
                    try:
                        if col_idx == 0:
                            worksheet.write(row_idx, col_idx, str(value), fmt)
                        else:
                            worksheet.write(row_idx, col_idx, float(value), fmt)
                    except:
                        worksheet.write(row_idx, col_idx, str(value), fmt)
            
            # ============ УСЛОВНОЕ ФОРМАТИРОВАНИЕ ============
            n_rows = len(df_clean)
            n_cols = len(df_clean.columns)
            
            if n_rows > 0 and n_cols > 1:
                worksheet.conditional_format(
                    1, 1,          # start_row, start_col (с первой строки данных, со второго столбца)
                    n_rows, n_cols - 1,  # end_row, end_col
                    {
                        'type': 'cell',
                        'criteria': '==',
                        'value': 0,
                        'format': zero_fmt
                    }
                )
            
            # ============ ЗАКРЕПЛЕНИЕ ============
            worksheet.freeze_panes(1, 1)  # Закрепляем первую строку и первый столбец
            
            # ============ ШИРИНА КОЛОНОК ============
            for col_idx, col_name in enumerate(df_clean.columns):
                # Собираем все значения для определения максимальной длины
                values = [str(col_name)]
                for row in df_clean.values:
                    val = row[col_idx]
                    if val is not None and not (isinstance(val, float) and np.isnan(val)):
                        values.append(str(val))
                
                max_len = max(len(v) for v in values) if values else 10
                worksheet.set_column(col_idx, col_idx, min(max_len + 2, 50))
            
            # ============ ЗАКРЫВАЕМ WORKBOOK ============
            workbook.close()
            workbook = None
            
            # Освобождаем память
            gc.collect()
            time.sleep(0.5)
            
            #print(f"✅ Временный файл создан: {temp_filename}")
            
            # ============ ПЫТАЕМСЯ ПЕРЕИМЕНОВАТЬ ============
            try:
                # Если оригинальный файл существует, пробуем удалить
                if os.path.exists(filename):
                    try:
                        os.remove(filename)
                        #print(f"🗑️ Старый файл удален")
                    except PermissionError:
                        # Не можем удалить - сохраняем с новым именем
                        new_filename = f"{base}_{timestamp}{ext}"
                        os.rename(temp_filename, new_filename)
                        print(f"✅ Файл сохранен как: {new_filename}")
                        print(f"ℹ️  Старый файл {filename} был занят")
                        return new_filename
                
                # Переименовываем временный файл
                os.rename(temp_filename, filename)
                print(f"✅ Файл сохранен как: {filename}")
                return filename
                
            except Exception as e:
                # Если не можем переименовать - оставляем временный файл
                print(f"⚠️ Не удалось переименовать: {e}")
                print(f"✅ Файл доступен как: {temp_filename}")
                return temp_filename
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            # Чистим временный файл
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except:
                    pass
            raise
    

    def ttv_pipeline(self, hist_file_path_full: str, hist_file_path_curr_year: str):
        """
            Пайплайн для обновления таблицы с историческими данными по статистике TTV.

            Параметры:
            ----------
                hist_file_path_full: str
                    Путь к файлу с историческими данными, начиная с 2021 г.
                hist_file_path_curr_year: str
                    Путь к файлу с историческими данными по текущему году.
            
            Returns:
                ttv_df_full_hist_updated: pd.DataFrame
                    Обновленная таблица со всеми историческими данными с 2021 г
                ttv_df_curr_year_updated: pd.DataFrame
                    Обновленная таблица с историческими данными по текущему году
        """
        # ШАГ 1. Выгрузка свежих данных за последний месяц из БД Mediascope
        data_new_df = self.make_api_calculation()

        # ШАГ 2. Обновление таблиц с историческими данными
        full_hist_df_updated, curr_year_df_updated = self.update_table(data_new_df, hist_file_path_full, hist_file_path_curr_year)

        # ШАГ 3. Обновление файлов с историческими данными
        self.save_with_xlsxwriter(df = full_hist_df_updated, filename = hist_file_path_full)
        self.save_with_xlsxwriter(df = curr_year_df_updated, filename = hist_file_path_curr_year)

        return {
            'свежие данные': data_new_df,
            'обновленные исторические данные с 2021г': full_hist_df_updated,
            'обновленные исторические данные тек год': curr_year_df_updated
        }
    


class VIMBUpdater:
    """
        Класс для обновления статистик, которые тянутся из ВИМБ (Отчет: Продукты и цены)
    """
    def __init__(self, vimb_file_path: str, guide_book_file_path: str, date_filter: list):
        self.vimb_file_path = vimb_file_path
        self.guide_book_file_path = guide_book_file_path
        self.date_filter = date_filter

        guide_book = pd.read_excel(guide_book_file_path)
        self.guide_df = guide_book[['Название канала в VIMB', 'ЕРК', 'ЖРК', 'МРК', 'ДРК']]
        self.guide_df.rename(columns = {'Название канала в VIMB': 'Канал'}, inplace = True)


        # Список виртуальных каналов
        self.virtual_channels = [
            'Единый рекламный канал',
            'Женский рекламный канал',
            'Мужской рекламный канал',
            'Детский рекламный канал',
        ]

        # Список месяцев
        self.months_list = [
            'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 
            'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
        ]

    
    def remove_empty_data_rows(self, df: pd.DataFrame, id_columns: list = None):
        """
            Удаляет строки, где есть значения только в id_columns, а в остальных NaN.

            Параметры:
            ----------
                df: pd.DataFrame
                    Таблица, для которой надо сделать преобразования
                id_columns: list
                    Список колонок, в которых должны присутствовать значения
        """
        if id_columns is None:
            id_columns = ['Вирт.канал / Месяц', 'Виртуальный канал', 'Месяц']
        
        # Список всех остальных колонок
        other_columns = [col for col in df.columns if col not in id_columns]
        
        # Оставляем строки, где есть хотя бы одно не-NaN значение в остальных колонках
        df = df[df[other_columns].notna().any(axis = 1)]
        
        return df
    

    def parse_vimb(self):
        """
            Метод для парсинга VIMB-файла с выгрузкой из отчёта "Продукты и цены"
        """
        # ШАГ 1. Чтение данных из файла
        df = pd.read_excel(self.vimb_file_path)
        
        # ШАГ 2. Создание и заполнение столбцов "Виртуальный канал" и "Месяц"
        df['Виртуальный канал'] = None
        df['Месяц'] = None

        current_month = None
        current_channel = None

        for i in range(len(df)):
            val = df.iloc[i, 0]  # значение в первом столбце
            
            if pd.notna(val):
                # Проверяем, что это виртуальный канал
                if val in self.virtual_channels:
                    current_channel = val
                    df.loc[i, 'Виртуальный канал'] = val
                # Проверяем, что это месяц
                elif any(month in str(val) for month in self.months_list):
                    current_month = val
                    df.loc[i, 'Месяц'] = val
            
            # Заполняем текущие значения
            df.loc[i, 'Месяц'] = current_month
            df.loc[i, 'Виртуальный канал'] = current_channel
        

        cleaned_df = self.remove_empty_data_rows(df)
        cleaned_df = cleaned_df.drop(['Вирт.канал / Месяц'], axis = 1)
        cleaned_df.reset_index(drop = True)

        need_columns = ['Виртуальный канал', 'Месяц', 'Канал', 'Рассчитать автом-ки', 'GRP (сред.)', 'Участие в объемах, % (текущее)']
        data = cleaned_df[need_columns].reset_index(drop = True)
        data.rename(columns = {
            'Рассчитать автом-ки': 'Метка горячая',
            'GRP (сред.)': 'tvr vimb', 
            'Участие в объемах, % (текущее)': 'КУЧ'
        }, inplace = True)
        data['КУЧ'] = data['КУЧ'] / 100.0

        # Отбираем только горячие месяцы
        data_closed_month = data[data['Метка горячая'] == 1.0].reset_index(drop = True)

        # Отбираем последний горячий месяц
        self.new_column_name = TTVUpdater(self.date_filter).generate_month_name()
        last_closed_month = data_closed_month[data_closed_month['Месяц'] == self.new_column_name].reset_index(drop = True)

        result_dict = {
            'ЕРК': last_closed_month[last_closed_month['Виртуальный канал'] == 'Единый рекламный канал'].reset_index(drop = True),
            'ЖРК': last_closed_month[last_closed_month['Виртуальный канал'] == 'Женский рекламный канал'].reset_index(drop = True),
            'МРК': last_closed_month[last_closed_month['Виртуальный канал'] == 'Мужской рекламный канал'].reset_index(drop = True),
            'ДРК': last_closed_month[last_closed_month['Виртуальный канал'] == 'Детский рекламный канал'].reset_index(drop = True),
        }

        return result_dict
    

    def tvr_output(self, result_dict: dict, statistic: str):
        """
            Метод для формирования выходной таблицы с tvr (рекламный рейтинг ВК ВИМБ)

            Параметры:
            ----------
                result_dict: dict
                    Словарь из свежих данных, которые необходимо добавить в файл с историческими данными.
                    Ключ - название ВК, Значение - датафрейм с данными
                statistic: str
                    Название статистики, для которой будем делать преобразование
            
            Returns:
            ----------
                tvr_results: dict
                    Словарь с результатами. 
                    Ключ - название ВК, Значение - датафрейм для записи в файл
        """
        statistic_results = {}
        for vk, result_df in result_dict.items():
            # Совмещаем по названию со справочником
            guide = self.guide_df[self.guide_df[vk] == 1]
            guide = guide[['Канал', vk]]
            # Отбираем только нужные столбцы из ВИМБа
            tvr_df = result_df[['Канал', statistic]]

            #new_channels = set(guide['Канал']) - set(tvr_df['Канал'])  # Новые каналы (есть в справочнике, нет в истории)

            # Если добавились новые каналы
            #if len(new_channels) > 0:
            #    print(Color.BOLD + Color.GREEN + f"Найдены следующие новые каналы в {vk}. Заполню нулями историю для этих каналов." + Color.END)
            #    for ch in new_channels:
            #        print(f"  - {ch}")

            # Непосредственное совмещение со справочником
            tvr_merged = pd.merge(tvr_df, guide, on = 'Канал', how = 'inner')
            tvr_merged = tvr_merged[['Канал', statistic]]
            tvr_merged.rename(columns = {statistic: self.new_column_name}, inplace = True)
            tvr_merged = tvr_merged.fillna(0)
            tvr_merged_sorted = tvr_merged.sort_values(by = 'Канал')
            
            statistic_results[vk] = tvr_merged_sorted.reset_index(drop = True)
        
        return statistic_results
    

    def vimb_pipeline(
            self, 
            tvr_vimb_hist_df_full_path: str, 
            tvr_vimb_current_year_df_path: str,
            kuch_hist_df_full_path: str, 
            kuch_current_year_df_path: str,
            ):
        """
            Пайплайн для обновления исторических файлов с tvr vimb, а также КУЧ

            Параметры:
            ----------
                tvr_vimb_hist_df_full_path: str
                    Путь к файлу с историческими данными tvr vimb, начиная с 2021 г
                tvr_vimb_current_year_df_path: str
                    Путь к файлу с историческими данными по текущему году tvr vimb
                kuch_hist_df_full_path: str
                    Путь к файлу с историческими данными КУЧ, начиная с 2021 г
                kuch_current_year_df_path: str
                    Путь к файлу с историческими данными по текущему году КУЧ
            
            Returns:
            ----------
                tvr_results: dict
                    Словарь с результатами tvr vimb. 
                    Ключ - название ВК, Значение - датафрейм для записи в файл
                kuch_results: dict
                    Словарь с результатами tvr vimb. 
                    Ключ - название ВК, Значение - датафрейм для записи в файл
        """
        # ШАГ 1. Парсинг выгрызки из Продуктов и Цен
        result_dict = self.parse_vimb()

        # ШАГ 2. Формирование таблиц с tvr
        tvr_results = self.tvr_output(result_dict, 'tvr vimb')

        # ШАГ 3. Формирование таблиц с КУЧ
        kuch_results = self.tvr_output(result_dict, 'КУЧ')

        # ШАГ 4. Обновление таблиц с историческими данными
        updater = DataUpdater(self.guide_df, 2026)

        tvr_curr_year_updated, tvr_full_hist_updated = updater.update_historical_data(
            tvr_results, tvr_vimb_hist_df_full_path, tvr_vimb_current_year_df_path
            )
        
        kuch_curr_year_updated, kuch_full_hist_updated = updater.update_historical_data(
            kuch_results, kuch_hist_df_full_path, kuch_current_year_df_path
            )
        
        # ШАГ 5. Сохранение данных обратно в файл
        ThematicShare.save_with_xlsxwriter(tvr_curr_year_updated, tvr_vimb_current_year_df_path)
        ThematicShare.save_with_xlsxwriter(tvr_full_hist_updated, tvr_vimb_hist_df_full_path)
        print('\n')
        ThematicShare.save_with_xlsxwriter(kuch_curr_year_updated, kuch_current_year_df_path)
        ThematicShare.save_with_xlsxwriter(kuch_full_hist_updated, kuch_hist_df_full_path)

        return tvr_results, kuch_results
    


class VIMBHistoryUpdater:
    """
        Класс для обновления исторических файлов с ВИМБ историей
    """
    def __init__(
            self, guide_filepath: str, 
            ttv_filepath: str, tvr_vimb_filepath: str,
            tvr_filepath: str, TVR_filepath: str,
            kuch_filepath: str, share_filepath: str
        ):
        self.guide_filepath = guide_filepath
        self.ttv_filepath = ttv_filepath
        self.tvr_vimb_filepath = tvr_vimb_filepath
        self.tvr_filepath = tvr_filepath
        self.TVR_filepath = TVR_filepath
        self.kuch_filepath = kuch_filepath
        self.share_filepath = share_filepath

        self.bca_dict = {
            'ЕРК': ['все 25-49', 'Единый рекламный канал', 18629, 158],
            'ЖРК': ['ж 25-49', 'Женский рекламный канал', 18685, 856],
            'МРК': ['м 25-49', 'Мужской рекламный канал', 18686, 857],
            'ДРК': ['все 4-40', 'Детский рекламный канал', 19723, 571]
            
        }
    

    def get_period_dates(self, month_str):
        """
            Преобразует строку вида 'Январь 2021' в (начало_периода, конец_периода)
        """
        # Разделяем на месяц и год
        month_name, year_str = month_str.split(' ')
        year = int(year_str)
        
        # Словарь для преобразования названий месяцев на русском
        months_ru = {
            'Январь': 1, 'Февраль': 2, 'Март': 3, 'Апрель': 4,
            'Май': 5, 'Июнь': 6, 'Июль': 7, 'Август': 8,
            'Сентябрь': 9, 'Октябрь': 10, 'Ноябрь': 11, 'Декабрь': 12
        }
        
        month_num = months_ru[month_name]
        
        # Начало периода - первый день месяца
        start_date = datetime(year, month_num, 1)
        
        # Конец периода - последний день месяца
        last_day = calendar.monthrange(year, month_num)[1]
        end_date = datetime(year, month_num, last_day)
        
        return start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y')
    

    def parse_one_vk(self, VK: str):
        """
            Метод для формирования отчета для одного ВК.

            Параметры:
            ----------
                VK: str
                    Название ВК, для которого будет делаться анализ
        """
        # ШАГ 1. Чтение файла-Справочник
        guide_df = pd.read_excel(self.guide_filepath, sheet_name = 'Sheet1')
        guide_df = guide_df[guide_df[VK] == 1].reset_index(drop = True)
        guide_df = guide_df[['ID канала VIMB', 'Название канала в VIMB', 'Холдинг', 'Группа']]
        guide_df['ЦА'] = self.bca_dict[VK][0]
        guide_df.rename(columns = {'Название канала в VIMB': 'Канал'}, inplace = True)

        # ШАГ 2. Чтение файла-TTV
        TTV = pd.read_excel(self.ttv_filepath, sheet_name = 'TTV')
        melted_TTV = pd.melt(TTV, id_vars = ['ЦА'], var_name = 'Месяц', value_name = 'TTV')

        # ШАГ 3. Чтение файла tvr vimb
        tvr_vimb = pd.read_excel(self.tvr_vimb_filepath, sheet_name = VK)
        melted_tvr_vimb = pd.melt(tvr_vimb, id_vars = ['Канал'], var_name = 'Месяц', value_name = 'tvr vimb')
        melted_tvr_vimb['ЦА'] = self.bca_dict[VK][0]
        merged_tvr_vimb = pd.merge(melted_tvr_vimb, guide_df, on = ['Канал', 'ЦА'], how = 'inner')

        # ШАГ 4. Чтение файла tvr рекламный
        tvr = pd.read_excel(self.tvr_filepath, sheet_name = VK)
        melted_tvr = pd.melt(tvr, id_vars = ['Канал'], var_name = 'Месяц', value_name = 'tvr рекламный')
        melted_tvr['ЦА'] = self.bca_dict[VK][0]
        merged_tvr = pd.merge(melted_tvr, guide_df, on = ['Канал', 'ЦА'], how = 'inner')

        # ШАГ 5. Чтение файла TVR эфира
        TVR = pd.read_excel(self.TVR_filepath, sheet_name = VK)
        melted_TVR = pd.melt(TVR, id_vars = ['Канал'], var_name = 'Месяц', value_name = 'TVR эфира')
        melted_TVR['ЦА'] = self.bca_dict[VK][0]
        merged_TVR = pd.merge(melted_TVR, guide_df, on = ['Канал', 'ЦА'], how = 'inner')

        # ШАГ 6. Формирование таблицы КУС, основываясь на таблице с TVR и tvr рекламный
        kus_df = pd.merge(merged_tvr, merged_TVR, on = ['Канал', 'Месяц', 'ЦА', 'ID канала VIMB', 'Холдинг', 'Группа'], how = 'inner')
        kus_df['КУС'] = kus_df['tvr рекламный'] / kus_df['TVR эфира']
        kus_df = kus_df[['Канал', 'ID канала VIMB', 'Месяц', 'ЦА', 'tvr рекламный', 'TVR эфира', 'КУС', 'Холдинг', 'Группа']]

        # ШАГ 7. Чтение файла КУЧ
        kuch = pd.read_excel(self.kuch_filepath, sheet_name = VK)
        melted_kuch = pd.melt(kuch, id_vars = ['Канал'], var_name = 'Месяц', value_name = 'КУЧ')
        melted_kuch['ЦА'] = self.bca_dict[VK][0]
        merged_kuch = pd.merge(melted_kuch, guide_df, on = ['Канал', 'ЦА'], how = 'inner')

        # ШАГ 8. Чтение файла Share
        share = pd.read_excel(self.share_filepath, sheet_name = VK)
        melted_share = pd.melt(share, id_vars = ['Канал'], var_name = 'Месяц', value_name = 'Доля')
        melted_share['ЦА'] = self.bca_dict[VK][0]

        # ШАГ 9. Формирование итоговой таблицы
        merged_share = pd.merge(melted_share, guide_df, on = ['Канал', 'ЦА'], how = 'inner')

        # прилепляем tvr vimb
        with_tvr_vimb = pd.merge(merged_share, merged_tvr_vimb, on = ['Канал', 'ЦА', 'Месяц', 'ID канала VIMB', 'Холдинг', 'Группа'], how = 'inner')

        # прилепляем кус и рейтинги
        with_kus = pd.merge(with_tvr_vimb, kus_df, on = ['Канал', 'ЦА', 'Месяц', 'ID канала VIMB', 'Холдинг', 'Группа'], how = 'inner')

        # прилепляем куч
        with_kuch = pd.merge(with_kus, merged_kuch, on = ['Канал', 'ЦА', 'Месяц', 'ID канала VIMB', 'Холдинг', 'Группа'], how = 'inner')

        # прилепляем TTV
        full_df = pd.merge(with_kuch, melted_TTV, on = ['ЦА', 'Месяц'], how = 'inner')

        full_df['Единый канал'] = self.bca_dict[VK][1]
        full_df['ID единого канала'] = self.bca_dict[VK][2]
        full_df['ID ЦА для ср. рейт.'] = self.bca_dict[VK][3]

        # Применяем функцию к столбцу 'Месяц'
        full_df[['Период с..', 'Период по..']] = full_df['Месяц'].apply(
            lambda x: pd.Series(self.get_period_dates(x))
        )
        full_df.rename(columns = {
            'Канал': 'Реальный канал', 
            'ID канала VIMB': 'ID канала',
            'ЦА': 'ЦА для ср. рейт.',
            
        }, inplace = True)


        full_df['Год'] = pd.to_datetime(full_df['Период с..']).dt.year

        # Создаем словарь месяцев
        months_ru = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }

        # Добавляем столбец с названием месяца
        full_df['Номер месяца'] = pd.to_datetime(full_df['Период с..'], format = '%d.%m.%Y', errors = 'coerce').dt.month
        full_df['Месяц'] = full_df['Номер месяца'].map(months_ru)


        full_df = full_df[
            [
                'Единый канал', 'ID единого канала', 'Период с..', 'Период по..', 
                'Реальный канал', 'ID канала', 'КУС', 'ЦА для ср. рейт.', 'ID ЦА для ср. рейт.', 
                'tvr рекламный', 'TVR эфира', 'КУЧ', 'Доля', 'TTV', 'tvr vimb', 'Холдинг', 'Группа', 'Год', 'Месяц', 'Номер месяца'
            ]
        ]

        full_df = full_df.fillna(0)
        full_df = full_df.replace([float('inf'), float('-inf')], 0)
        sorted_df = full_df.sort_values(by = ['Реальный канал', 'Год'], ascending = [True, True]).reset_index(drop = True)

        return sorted_df
    


    def save_formatted_excel(self, df_dict, filename = 'output.xlsx'):
        """
            Сохраняет словарь датафреймов в Excel с форматированием
            
            Параметры:
            ----------
                df_dict: dict
                    Cловарь {название_листа: датафрейм}
                filename: str
                    Имя выходного файла
        """
        # Ширина столбцов в пикселях
        COLUMN_WIDTHS = {
            'Единый канал': 180, 
            'ID единого канала': 95, 
            'Период с..': 95,
            'Период по..': 95, 
            'Реальный канал': 223, 
            'ID канала': 95,
            'КУС': 95, 
            'ЦА для ср. рейт.': 95, 
            'ID ЦА для ср. рейт.': 95,
            'tvr рекламный': 95, 
            'TVR эфира': 95, 
            'КУЧ': 95, 
            'Доля': 95,
            'TTV': 95, 
            'tvr vimb': 95, 
            'Холдинг': 110, 
            'Группа': 60,
            'Год': 77, 
            'Месяц': 77, 
            'Номер месяца': 77
        }
        
        NUMERIC_COLUMNS = ['КУС', 'tvr рекламный', 'TVR эфира', 'КУЧ', 'Доля', 'TTV', 'tvr vimb']
        INTEGER_COLUMNS = ['Год', 'Номер месяца']
        
        # Цвета для листов
        SHEET_COLORS = {
            'ЕРК': '#6AF495', 'ЖРК': '#F4B6EB', 
            'МРК': '#85B6FF', 'ДРК': '#F9F48B'
        }
        
        with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
            workbook = writer.book
            
            for sheet_name, df in df_dict.items():
                # Форматируем датафрейм
                df = df.copy()
                for col in NUMERIC_COLUMNS:
                    if col in df.columns:
                        df[col] = df[col].apply(lambda x: round(x, 5) if pd.notna(x) else x)
                for col in INTEGER_COLUMNS:
                    if col in df.columns:
                        df[col] = df[col].astype('Int64')
                
                # Записываем
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                worksheet = writer.sheets[sheet_name]

                # Устанавливаем высоту первой строки (50 пикселей)
                worksheet.set_row(0, 50)
                
                # Формат для заголовков
                color = SHEET_COLORS.get(sheet_name, '#D9E1F2')
                header_format = workbook.add_format({
                    'bold': True,
                    'font_color': 'black',
                    'bg_color': color,
                    'align': 'center',
                    'valign': 'vcenter',
                    'text_wrap': True,
                    'border': 0,
                    'font_size': 14,
                    'font_name': 'Arial'
                })
                
                # Формат для данных (выравнивание по центру)
                data_format = workbook.add_format({
                    'align': 'center',
                    'valign': 'vcenter',
                    'font_size': 11,
                    'font_name': 'Arial'
                })
                
                # Формат для чисел с 5 знаками
                number_format = workbook.add_format({
                    'num_format': '0.00000',
                    'align': 'center',
                    'valign': 'vcenter',
                    'font_size': 11,
                    'font_name': 'Arial'
                })
                
                # Формат для целых чисел
                int_format = workbook.add_format({
                    'num_format': '0',
                    'align': 'center',
                    'valign': 'vcenter',
                    'font_size': 11,
                    'font_name': 'Arial'
                })
                
                # Записываем заголовки с форматированием
                for col_idx, col_name in enumerate(df.columns):
                    worksheet.write(0, col_idx, col_name, header_format)
                
                # Записываем данные с форматированием
                for row_idx, row in enumerate(df.values, start=1):
                    for col_idx, value in enumerate(row):
                        col_name = df.columns[col_idx]
                        
                        # Выбираем формат в зависимости от типа данных
                        if col_name in NUMERIC_COLUMNS:
                            if pd.notna(value):
                                worksheet.write_number(row_idx, col_idx, float(value), number_format)
                            else:
                                worksheet.write(row_idx, col_idx, value, data_format)
                        elif col_name in INTEGER_COLUMNS:
                            if pd.notna(value):
                                worksheet.write_number(row_idx, col_idx, int(value), int_format)
                            else:
                                worksheet.write(row_idx, col_idx, value, data_format)
                        else:
                            worksheet.write(row_idx, col_idx, value, data_format)
                
                # Устанавливаем ширину столбцов
                for col_idx, col_name in enumerate(df.columns):
                    width_pixels = COLUMN_WIDTHS.get(col_name, 95)
                    worksheet.set_column(col_idx, col_idx, width_pixels / 7.5)
                
                # Закрепляем шапку
                worksheet.freeze_panes(1, 0)
                
                # Добавляем фильтры
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
        
        print(f"✅ Файл сохранен: {filename}")
    

    def vimb_history_pipeline(self, output_filename: str):
        """
            Пайплайн для обновления файла ВИМБ история.

            Параметры:
            ----------
                output_filename: str
                    Имя выходного файла с историей
        """
        # ШАГ 1. Формирование ВИМБ таблиц для каждого ВК
        vk_results = {}

        for vk, values in self.bca_dict.items():
            vk_results[vk] = self.parse_one_vk(vk)
        
        # ШАГ 2. Сохранение в файл
        self.save_formatted_excel(vk_results, filename = output_filename)
        return vk_results