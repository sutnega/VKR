Основной сбор информации 
"""python main.py export --format xlsx --output news.xlsx

 python main.py list -v     
  python main.py collect     
"""

"""# просто общий топ слов
python analyze_words.py

# топ-50 слов
python analyze_words.py --top 50

# общий топ + по каждому источнику отдельно
python analyze_words.py --by-source --top 20"""

запуск Веб анализа """streamlit run news_app.py"""
