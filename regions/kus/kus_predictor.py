import pandas as pd
from pandas.tseries.offsets import DateOffset
import os
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

from OMA_tools.io_data.operations import File, Table, Dict_Operations
from OMA_tools.io_data.dates import Dates_Operations
from OMA_tools.io_data.colors import *

from OMA_tools.regions.data_extraction.export_data import *


MONTHS_RU = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}


BCA_NEED_REPLACE = {
        'All 18+': 'ВСЕ 18+',
        'All 14-59': 'ВСЕ 14-59',
        'All 14-44': 'ВСЕ 14-44',
        'All 25-54': 'ВСЕ 25-54',
        'All 6-54': 'ВСЕ 6-54',
        'All 10-45': 'ВСЕ 10-45',
        'All 14-54': 'ВСЕ 14-54',
        'All 4-45': 'ВСЕ 4-45',
        'All 25-49': 'ВСЕ 25-49',
        'W 25-59': 'Ж 25-59',
        'W 14-44': 'Ж 14-44'
}

REPLACEMENTS = {
            'телеканал': 'канал',
            'санкт-петербург санкт-петербург': 'телеканал санкт-петербург',
            'первый канал': 'первый',
            'пятый канал': '5 канал',
            'тв-3': 'тв3'
}



class KUSCalculation:
    """
        Класс для выгрузки данных по Эфирному рейтингу для дальнейшего прогнозирования КУСа.
    """
    def __init__(self, date_filter):
        self.date_filter = date_filter

        target_date = pd.to_datetime(self.date_filter[0][0])
        self.target_date_str = f"{MONTHS_RU[target_date.month]} {target_date.year}"
    

    @staticmethod
    def format_time(time_int):
        """
            Вспомогательный метод для преобразования временного слота
        """
        if time_int[:2] in ['24', '25', '26', '27', '28', '29']:
            time_int = str(int(time_int[:2]) - 24) + '0000'

        time_str = str(time_int).zfill(6)   

        return f'{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}'


    def calculation(
            self, 
            tasks_json_full: dict, 
            columns_order_path: str,
            by_slots: bool = False
        ):
        """
            Метод для выгрузки данных из API.

            Параметры:
            ----------
                tasks_json_full: dict:
                    Словарь из сформированных задач в формате JSON.
                columns_order_path: str
                    Путь к файлу с нужным порядком колонок.
                by_slots: bool
                    Если True, происходит выгрузка по слотам. Нужна для закрытия месяца (!!!).
                    В противном случае делается обычная выгрузка рейтингов.
            
            Returns:
            ----------
                pd.DataFrame
                    Отсортированная итоговая таблица.
        """
        # ШАГ 1. Загрузка порядка колонок
        columns_order = Dict_Operations.load_pkl_file(columns_order_path)

        columns_table = pd.concat([
            pd.DataFrame({'tvCompanyName': cols[1:], 'ЦА': bca})
            for bca, cols in columns_order.items()
        ]).reset_index(drop = True)

        # ШАГ 2. Выгрузка данных через API
        import time
        start = time.perf_counter()

        results = {}
        exp_serv = ExportService(self.date_filter)

        export_kwargs = dict(
            export_func = exp_serv.export_period,
            statistics = ['RtgPer'],
        )
        if by_slots:
            export_kwargs['slices'] = ['tvCompanyName', 'regionName', 'timeBand60']

        export = partial(exp_serv.export_one, **export_kwargs)

        with ThreadPoolExecutor(max_workers = 8) as executor:
            futures = {
                executor.submit(export, bca, task_json): bca
                for bca, task_json in tasks_json_full.items()
            }
            for future in as_completed(futures):
                bca, df = future.result()
                results[bca] = df
        
        end = time.perf_counter()
        print('\n')
        print(Color.BOLD + Color.GREEN + '=== ДАННЫЕ ВЫГРУЖЕНЫ. ДЕЛАЮ ПРЕОБРАЗОВАНИЯ. ===' + Color.END)
        print(f'Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.')

        # ШАГ 4. Приведение слотов (только если by_slots)
        if by_slots:
            for bca, df in results.items():
                df['timeBand60'] = df['timeBand60'].apply(KUSCalculation.format_time)
        

        # ШАГ 5. МАНИПУЛЯЦИИ С "РОССИЯ 1" И КАНАЛОВ "ГТРК" ДЛЯ АУДИТОРИИ ВСЕ 18+
        all_18 = results['All 18+'].copy()
        all_14_44 = results['All 14-44'].copy()

        merge_kwargs = dict(
            all_18_df=all_18,
            all_14_44_df=all_14_44,
            statistic='RtgPer',
        )
        if by_slots:
            merge_kwargs['columns_to_merge'] = ['regionName', 'ЦА', 'timeBand60']
            merge_kwargs['include_time_band'] = True
        else:
            merge_kwargs['columns_to_merge'] = ['regionName', 'ЦА']
            merge_kwargs['include_time_band'] = False

        all_18_new, all_14_44_new = exp_serv.sum_2_channels(**merge_kwargs)
        results['All 18+'] = all_18_new
        results['All 14-44'] = all_14_44_new

        # ШАГ 6. Итоговый датафрейм
        result_df = pd.concat(results.values()).reset_index(drop = True)
        results_new = pd.merge(
            columns_table, result_df,
            on=['tvCompanyName', 'ЦА'], how = 'inner'
        ).reset_index(drop = True)
        results_new['Month'] = self.target_date_str

        rename_map = {
            'tvCompanyName': 'Channel',
            'RtgPer': 'TVR',
            'ЦА': 'BCA',
        }
        if by_slots:
            rename_map['timeBand60'] = 'time'

        results_new.rename(columns = rename_map, inplace = True)

        output_cols = ['Month', 'TVR', 'Channel', 'BCA']
        if by_slots:
            output_cols = ['Month', 'time', 'TVR', 'Channel', 'BCA']

        results_new = results_new[output_cols]

        # Замена значений на русские в столбце BCA
        results_new['BCA'].replace(BCA_NEED_REPLACE, inplace = True)
        # Убираем скобки из названий каналов-городов
        results_new['Channel'] = results_new['Channel'].str.replace(r'[()]', '', regex = True)

        results_new[['Channel', 'BCA']] = (
            results_new[['Channel', 'BCA']].apply(lambda s: s.str.lower())
        )

        sorted_df = results_new.sort_values(['Channel', 'BCA']).reset_index(drop = True)

        for old, new in REPLACEMENTS.items():
            sorted_df['Channel'] = sorted_df['Channel'].str.replace(old, new, regex = False)

        return sorted_df


    def make_ouput_table(
            self,
            tasks_json_fact_month: dict,
            columns_order_path: str,
            output_path: str,
            jasks_json_by_slots: dict = None,
            close_month: bool = False
        ):
        """
            Метод для формирования выходной таблицы с выгрузкой в зависимотси от того, какое сегодня обновление: закрытие месяца или обычное.

            Параметры:
            ----------
                tasks_json_fact_month: dict
                    Задачи в формате JSON для расчета фактической части месяца (применительно для обновления в середине месяца).
                columns_order_path: str
                    Путь к файлу с нужным порядком колонок.
                output_path: str
                    Путь к папке, куда сохраняются исторические данные.
                jasks_json_by_slots: dict
                    Задачи в формате JSON для расчета рейтингов по слотам (применительно для закрытия месяца).
                close_month: bool
                    Если True, то месяц закрывается. В противном случае производится обычное обновление в середине месяца. 
                    По умолчанию False.

            Returns:
            ----------
                api_results: list
        """
        api_results = []

        # Закрытие месяца
        if close_month == True:

            print(Color.BOLD + Color.CRIMSON + '=== 🚀 СЕГОДНЯ ПРОИЗВОЖУ ЗАКРЫТИЕ ПРОШЛОГО МЕСЯЦА. ===' + Color.END)
            print(
                Color.BOLD + Color.CRIMSON + \
                'ДЕЛАЮ ВЫГРУЗКУ ВСЕГО ПРОШЛОГО МЕСЯЦА, А ТАКЖЕ ВЫГРУЗКУ ВСЕГО ПРОШЛОГО МЕСЯЦА В РАЗБИВКЕ ПО СЛОТАМ.' + \
                Color.END + '\n'
            )
            print(Color.NAVY + 'ВЫГРУЖАЮ ПРЕДЫДУЩИЙ МЕСЯЦ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + Color.END + '\n')

            fact_month = self.calculation(tasks_json_fact_month, columns_order_path)
            print('✅ Данные за фактическую часть месяца выгружены. Спасибо за ваше ожидание! 😊' + '\n')

            print(Color.NAVY + 'ВЫГРУЖАЮ ПРЕДЫДУЩИЙ МЕСЯЦ С РАЗБИВКОЙ ПО СЛОТАМ. ПОЖАЛУЙСТА, ПОДОЖДИТЕ ...' + Color.END + '\n')
            full_month_by_slots = self.calculation(jasks_json_by_slots, columns_order_path, by_slots = True)
            print('✅ Данные за предыдущий месяц выгружены. Спасибо за ваше ожидание! 😊' + '\n')

            # Объединяем две выгрузки в одну, причем для 
            full_results = full_month_by_slots.merge(
                fact_month, 
                how  = 'left', 
                on = ['Month', 'Channel', 'BCA'], 
                suffixes = ('', ' Total')
            )

            # Загружаем в эксель историрование по месяцам
            column_configs = [
                {'header': 'Month',     'width': 14.0, 'format': 'text'},
                {'header': 'time',      'width': 10.0, 'format': 'time'},
                {'header': 'TVR',       'width': 12.0, 'format': 'number'},
                {'header': 'Channel',   'width': 28.0, 'format': 'text'},
                {'header': 'BCA',       'width': 14.0, 'format': 'text'},
                {'header': 'TVR Total', 'width': 12.0, 'format': 'number'},
            ]

            filename = output_path + self.target_date_str + '.xlsx'

            try:
                with pd.ExcelWriter(filename, engine = 'xlsxwriter') as writer:
                    Builder().make_style_of_table(
                        writer = writer,
                        df = full_results,
                        sheet_name = 'Sheet1',
                        column_configs = column_configs,
                    )
            except Exception as e:
                print(f"❌ Не удалось сохранить файл {filename}: {e}")
                raise
            else:
                print(f"✅ Файл сохранён: {filename}")

            api_results = [fact_month, full_results]
        
        # Обновление в середине месяца
        else:

            print(
                Color.BOLD + Color.ORANGE + \
                '=== 🚀 СЕГОДНЯ ОБНОВЛЕНИЕ В СЕРЕДИНЕ МЕСЯЦА. ДЕЛАЮ ВЫГРУЗКУ ФАКТИЧЕСКОЙ ЧАСТИ МЕСЯЦА. ===' + \
                Color.END + '\n'
            )

            fact_month = self.calculation(tasks_json_fact_month, columns_order_path)
            print('✅ Данные за фактическую часть месяца выгружены. Спасибо за ваше ожидание! 😊' + '\n')
            api_results = [fact_month, None]
        
        return api_results


    






class KUS_Forecast:
    def __init__(self, table):
        self.table = table
    
    
    @staticmethod
    def sdate(row):
        return datetime.strptime(row,'%B %Y')
    
    
    @staticmethod
    def div(numer, denom):
        return lambda row: 0 if row[denom] == 0 else row[numer] / row[denom]
    
    
    @staticmethod
    def read_excel_from_start_marker(file_path: str, start_marker: str = 'Начало периода') -> pd.DataFrame:
        """
            Метод для чтения файла. Файл начинает считываться только тогда, когда найдена определенная строка.
            Метод возвращает количество строк, которое нужно пропустить в файле для успешного его чтения.
        """
        # Читаем весь файл для поиска маркера
        df_temp = pd.read_excel(file_path, header = None)

        # Ищем строку с маркером
        start_row = None
        for idx, row in df_temp.iterrows():
            if any(str(cell).strip() == start_marker for cell in row if pd.notna(cell)):
                start_row = idx + 1  # +1 потому что header=None, данные начинаются со следующей строки
                break

        if start_row is None:
            raise ValueError(f"Маркер '{start_marker}' не найден в файле")
        
        return start_row - 1
    
    
    @staticmethod
    def from_file(path, skip = 0, col_list = [], is_vimb = True):
        """
            Метод для чтения данных из папки
        """
        files = []
        files += os.listdir(path)

        result = pd.DataFrame()
        for months in files:
            if is_vimb:
                num_skiprows = KUS_Forecast.read_excel_from_start_marker(path + months)
                table = pd.read_excel(path + months, skiprows = num_skiprows, usecols = col_list)
            else:
                table = pd.read_excel(path + months, skiprows = skip, usecols = col_list)
            result = pd.concat([result, table], ignore_index = True)

        return result
    
    
    @staticmethod
    def parse_VIMB(table, is_current_month = True):
        """
            Метод для парсинга файлов с выгрузкой из ВИМБа.
            flag = True: текущий месяц
            flag = False: закрытие месяца
        """
        table = table.rename(columns = {'Время эфира с' : 'time',
                                        'Начало периода':'Month',
                                        'Открытый объем без учёта квот' : 'Volume',
                                        'Открытый WGRP без учета квот' : 'GRP',
                                        'Базовая ЦА' : 'BCA',
                                        'Ср. рейтинг' : 'tvr_adv',
                                        'Канал': 'Channel'})  

        table['BCA'] = table['BCA'].str.split(' ', expand = True)[0].str.lower() + ' ' + table['BCA'].str.split(' ', expand = True)[1]
        table['Channel'] = table['Channel'].str.lower()
    
        if is_current_month is not True:
            table['time'] = table['time'].astype(str)

        if not pd.api.types.is_datetime64_any_dtype(table['Month']):
            table['Month'] = pd.to_datetime(table['Month'])
        
        # Теперь применяем strftime ко всему столбцу (векторизованно, без цикла)
        table['Month'] = table['Month'].dt.strftime('%B %Y')

        return table
    
    
    def predict(self, data_api: pd.DataFrame, first_date, last_date, filepath_vimb_curr_month: str):
        """
            Метод для непосредственного прогнозирования КУС
            Args:
                data_api: pd.DataFrame: Выгрузка с TVR из БД Mediascope
                first_date: Дата начала вывода данных
                last_date: Дата окончания вывода данных
                filepath_vimb_curr_month: str: путь к файлу с выгрузкой из VIMB по текущему месяцу
            Returns:
                df_result_all: pd.DataFrame: Датафрейм с прогнозом
        """
        
        today = date.today()
        # дата начала и окончания исторических данных для предсказания куса (база прогноза)
        first_fact_date = today - pd.offsets.MonthBegin()- DateOffset(months = 6) #1 число пол года назад
        last_fact_date = today - pd.offsets.MonthBegin()    
        table = self.table

        ################################### ОБЩАЯ ПОДГОТОВКА #############################################################
        table['Month'] = table['Month'].apply(KUS_Forecast.sdate)
        table['PredWeight'] = table['TVR'] / table['TVR Total']
        table['PredKM'] = table.apply(KUS_Forecast.div('tvr_adv', 'TVR'), axis = 1)
        table = table.astype({"PredKM": "float64"})


        ############################# ПОДСЧЕТ ЗАКРЫТЫХ МЕСЯЦЕВ ВСЕ КАНАЛЫ СРАЗУ #####################################################
        zakr_piv = table[(table['Month'] >= first_date) & (table['Month'] < last_fact_date)]
        zakr_piv = zakr_piv.pivot_table(index = 'Channel', columns = ['Month'], values = ['TVR Total', 'Volume', 'GRP'],
                         aggfunc = {'TVR Total': 'mean', 'Volume': 'sum','GRP': 'sum'})

        if zakr_piv.empty != True:
            df_zakrytye = zakr_piv['GRP'] * 20 / (zakr_piv['Volume'] * zakr_piv['TVR Total'])
            
        else:
            df_zakrytye=pd.DataFrame(index=pd.Index(table['Channel'].unique(),name='Channel'))
            print('Нет месяцев в факте!')


        ############################# ПРОГНОЗ МЕСЯЦЕВ ВСЕ КАНАЛЫ СРАЗУ #####################################################
        short_pred = table[(table['Month'] >= last_fact_date) & (table['Month'] <= last_date)]
        short_tmp = table[(table['Month'] >= first_fact_date) & (table['Month'] < last_fact_date)]

        tab_for_fill = short_tmp.pivot_table(index = ['Channel', 'time'],values = ['PredWeight', 'PredKM'],
                                             aggfunc = {'PredWeight': 'mean', 'PredKM': 'median'})

        ans = pd.merge(short_pred[['Channel', 'Month', 'time', 'BCA', 'Volume']], tab_for_fill, how = "left", on = ['Channel', "time"])
        
        ans['PredKM']=ans.groupby('Channel')['PredKM'].transform(lambda x: x.fillna(x.rolling(2, min_periods=1).mean().ffill().bfill()))
        
        df_prognoz = ans.pivot_table(index = ['Channel'], columns = 'Month', values = ['PredWeight'], aggfunc = lambda x: 
                        (ans.loc[x.index, 'PredWeight'] * ans.loc[x.index,'PredKM'] * ans.loc[x.index, 'Volume']).sum()/
                        ans.loc[x.index, 'Volume'].sum()).droplevel(0, axis = 1)

        ############################# ПОДСЧЕТ ТЕКУЩЕГО МЕСЯЦА #####################################################
        # Если текущий месяц январь
        if today.month == 1:
            if today.day > 14:
                print('Реализуем обновление в середине месяца')
                if today.day < 21:
                    fact_w = 0.5
                    prognoz_w = 0.5
                else:
                    fact_w = 0.7
                    prognoz_w = 0.3     

                # Загрузка данных вимб и апи
                adv_tec = pd.read_excel(filepath_vimb_curr_month,
                                    skiprows = 19,
                                    usecols = [0, 5, 6, 7, 12, 13])
                adv_tec = KUS_Forecast.parse_VIMB(adv_tec)
                tvr_prog_tec = data_api
                tec_main = pd.merge(tvr_prog_tec, adv_tec, how = "right", on = ['Channel', 'BCA', "Month"])

                # Подсчет факта
                tec_main['kus_f'] = tec_main['tvr_adv'] / tec_main['TVR']
                results_prognoz = pd.merge(df_prognoz, tec_main[['kus_f','Channel']], how = "left", on = ['Channel'])
                results_prognoz['tec_m_progn'] = results_prognoz.iloc[:,1]

                results_prognoz.iloc[:, 1] = results_prognoz['kus_f'] * fact_w + (results_prognoz.iloc[:, 1]) * prognoz_w

                df_result_all = df_zakrytye.merge(results_prognoz,on = 'Channel', how = 'left')

            else:
                print('Закрытие месяца!')
                df_result_all = df_zakrytye.merge(df_prognoz, on = 'Channel', how = 'left')

        # Если текущий месяц не январь
        else:
            if today.day > 10:
                print('Реализуем обновление в середине месяца')
                if today.day < 21:
                    fact_w = 0.5
                    prognoz_w = 0.5
                else:
                    fact_w = 0.7
                    prognoz_w = 0.3     

                    #Загрузка данных вимб и апи
                adv_tec = pd.read_excel(filepath_vimb_curr_month,
                                    skiprows = 19,
                                    usecols = [0, 5, 6, 7, 12, 13])
                adv_tec = KUS_Forecast.parse_VIMB(adv_tec)
                tvr_prog_tec = data_api
                print(adv_tec)
                print(adv_tec.dtypes)
                tec_main = pd.merge(tvr_prog_tec, adv_tec, how = "right", on = ['Channel', 'BCA', "Month"])

                        #Подсчет факта
                tec_main['kus_f'] = tec_main['tvr_adv'] / tec_main['TVR']
                results_prognoz = pd.merge(df_prognoz, tec_main[['kus_f','Channel']], how = "left", on = ['Channel'])
                results_prognoz['tec_m_progn'] = results_prognoz.iloc[:,1]

                results_prognoz.iloc[:, 1] = results_prognoz['kus_f'] * fact_w + (results_prognoz.iloc[:, 1]) * prognoz_w

                df_result_all = df_zakrytye.merge(results_prognoz,on = 'Channel', how = 'left')

            else:
                print('Закрытие месяца!')
                df_result_all = df_zakrytye.merge(df_prognoz, on = 'Channel', how = 'left')

        return df_result_all